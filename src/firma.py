"""Firma e integridad del PDF del Formulario 210.

Dos niveles, del más débil al más fuerte:

1. **Sello de integridad** (`sello_integridad`): un SHA-256 del archivo, del que
   se imprime un código corto en el PDF. No requiere certificados: permite
   detectar que el documento entregado es el mismo que se generó.

2. **Firma PAdES** (`firmar_pdf`): firma criptográfica del PDF con un
   certificado X.509 del contador o del contribuyente (archivo `.p12`/`.pfx`).
   Da integridad *y* no repudio: acredita quién produjo el borrador.

ADVERTENCIA LEGAL — ninguno de los dos presenta la declaración ante la DIAN.
El Formulario 210 se radica únicamente en el portal MUISCA con la Firma
Electrónica que la DIAN emite al contribuyente y que está atada a su RUT. Un
PDF firmado localmente no es una declaración presentada.
"""
import hashlib
import hmac
from pathlib import Path
from typing import Any, Dict, Optional, Union

AVISO_LEGAL = (
    "Esta firma acredita la integridad y el origen de este borrador. NO constituye "
    "presentación ante la DIAN, que se realiza únicamente en el portal MUISCA con la "
    "Firma Electrónica emitida por esa entidad."
)

CAMPO_FIRMA = "FirmaBorrador"


class FirmaError(Exception):
    """Certificado inválido, contraseña incorrecta o PDF no firmable."""


# --------------------------------------------------------------------------
# 1. Sello de integridad (sin certificados)
# --------------------------------------------------------------------------

def sello_integridad(ruta: Union[str, Path]) -> str:
    """SHA-256 del archivo, en hexadecimal."""
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def codigo_verificacion(ruta: Union[str, Path]) -> str:
    """Código corto y legible derivado del sello, para imprimir en el pie del PDF."""
    h = sello_integridad(ruta).upper()
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def verificar_sello(ruta: Union[str, Path], sello_esperado: str) -> bool:
    """Compara el sello del archivo con uno previamente emitido."""
    return hmac.compare_digest(sello_integridad(ruta), sello_esperado.lower())


# --------------------------------------------------------------------------
# 2. Firma PAdES
# --------------------------------------------------------------------------

def firmar_pdf(
    ruta_pdf: Union[str, Path],
    certificado: bytes,
    passphrase: str,
    razon: str = "Borrador Formulario 210",
    salida: Optional[Union[str, Path]] = None,
) -> Path:
    """Firma un PDF con un certificado PKCS#12 y devuelve la ruta del firmado.

    `certificado` son los bytes del `.p12`/`.pfx` — se leen de memoria y nunca
    se escriben a disco. Ni el certificado ni la `passphrase` deben registrarse
    en logs.
    """
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    ruta_pdf = Path(ruta_pdf)
    salida = Path(salida) if salida else ruta_pdf.with_name(f"{ruta_pdf.stem}_firmado.pdf")

    try:
        firmante = signers.SimpleSigner.load_pkcs12_data(
            pkcs12_bytes=certificado, other_certs=(),
            passphrase=passphrase.encode("utf-8") if passphrase else None)
    except Exception as e:                       # contraseña errada o archivo corrupto
        raise FirmaError(
            "No se pudo abrir el certificado. Revise que sea un archivo .p12/.pfx "
            "válido y que la contraseña sea correcta."
        ) from e
    if firmante is None:
        raise FirmaError("El certificado no contiene una llave privada utilizable.")

    with open(ruta_pdf, "rb") as fh:
        escritor = IncrementalPdfFileWriter(fh)
        firmado = signers.sign_pdf(
            escritor,
            signers.PdfSignatureMetadata(field_name=CAMPO_FIRMA, reason=razon),
            signer=firmante,
        )
        salida.parent.mkdir(parents=True, exist_ok=True)
        with open(salida, "wb") as out:
            out.write(firmado.getvalue())
    return salida


def validar_firma(ruta_pdf: Union[str, Path]) -> Dict[str, Any]:
    """Estado de la firma de un PDF.

    Devuelve `integro`: si el contenido cubierto por la firma NO fue alterado
    desde que se firmó. Es la garantía que importa aquí. No se valida la cadena
    de confianza contra una autoridad certificadora: el certificado del propio
    firmante se toma como raíz, porque el objetivo es detectar alteraciones y
    saber quién firmó, no acreditar a la autoridad emisora.
    """
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    with open(ruta_pdf, "rb") as fh:
        lector = PdfFileReader(fh)
        firmas = lector.embedded_signatures
        if not firmas:
            return {"firmado": False, "integro": False, "firmante": "", "razon": ""}

        firma = firmas[0]
        contexto = ValidationContext(trust_roots=[firma.signer_cert], allow_fetching=False)
        estado = validate_pdf_signature(firma, contexto)
        return {
            "firmado": True,
            "integro": bool(estado.intact),
            "firmante": firma.signer_cert.subject.human_friendly,
            "razon": (firma.sig_object.get("/Reason") or ""),
        }
