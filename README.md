# Declaración de Renta — Formulario 210 (AG 2025)

Herramienta interactiva (CLI) que ayuda a una persona natural residente en
Colombia a preparar el **borrador** de su Declaración de Renta (Formulario 210)
a partir de:

1. El archivo de **Información Exógena** descargado del portal DIAN
   ("Consulta de información reportada por terceros", `.xlsx`).
2. Una **entrevista interactiva** para los datos que la exógena no trae
   (patrimonio detallado, deudas propias, dependientes, costos, etc.).

Produce un **Excel con la hoja `FORMULARIO 210` diligenciada renglón por
renglón** (sobre la plantilla ITGS), un **resumen de la liquidación privada**
en consola y un **log de trazabilidad** de qué fila de la exógena alimentó
cada renglón.

> ⚠️ **Este proyecto genera un borrador de apoyo. NO reemplaza la asesoría de
> un contador o abogado tributarista.** Verifique la UVT, los topes y las
> tarifas contra la normativa DIAN vigente antes de presentar una declaración.

---

## Instalación

Requiere Python 3.9+ (probado con el Python 3.9.6 de macOS; el código no usa
sintaxis de versiones posteriores).

```bash
cd declaracion-renta
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Uso

### Aplicación web (arrastrar y soltar)

```bash
.venv/bin/python webapp.py        # abre http://127.0.0.1:5210
```

o doble clic en `abrir-app-web.command` desde Finder. Arrastre el `.xlsx` de
exógena a la página: se muestran los topes y la obligación de declarar, y un
formulario editable con **todas las cédulas de rentas** (trabajo, honorarios,
capital, no laborales, pensiones, dividendos), **dependientes económicos**,
**patrimonio y deudas**, ganancias ocasionales, retenciones y saldos. Cada
cambio recalcula la liquidación al instante y el botón *Descargar* genera el
Formulario 210 en Excel. Todo corre localmente (127.0.0.1), nada sale del
computador.

### Flujo interactivo por consola

```bash
.venv/bin/python run.py procesar /ruta/a/reporteExogena2025.xlsx
```

El flujo:

1. Muestra el aviso legal.
2. Parsea la exógena, valida los 5 **Topes** contra el resumen de la DIAN y
   explica si está **obligado a declarar** y por qué. Aplica la titularidad
   real (**beneficiario económico**): si la "Información Adicional" trae un
   `Porcentaje de Participación` menor al 100%, el valor se ajusta a esa
   participación; si hay varios propietarios o cotitulares sin porcentaje, la
   partida queda marcada para que confirme qué parte le corresponde (la DIAN
   reporta el 100% al titular principal).
3. Muestra un **resumen editable**: cada partida con su tercero informante y
   el renglón asignado; puede excluir partidas, reasignar renglones o corregir
   valores antes de continuar.
4. **Entrevista** por secciones (datos personales, dependientes, patrimonio no
   reportado, costos, pensiones, ganancias ocasionales, saldos del año
   anterior). El progreso se guarda en `sessions/sesion.json` tras cada
   sección: si interrumpe con Ctrl-C puede retomar con:

   ```bash
   .venv/bin/python run.py continuar
   ```

5. Calcula la liquidación, muestra el resumen y genera
   `output/Formulario210_<NIT>.xlsx` + `output/Formulario210_<NIT>.log.txt`.

### Modo no interactivo (demos, scripts, pruebas)

```bash
.venv/bin/python run.py procesar tests/fixtures/reporteExogena2025Elizabeth.xlsx \
    --no-interactivo --respuestas ejemplo_respuestas_elizabeth.json
```

`--respuestas` acepta un JSON `{clave: valor}` con las mismas claves de la
entrevista (ver `ejemplo_respuestas_elizabeth.json` y `src/entrevista.py`).

### Opciones

| Opción | Descripción |
|---|---|
| `--plantilla RUTA` | Plantilla Excel destino (por defecto la ITGS de `tests/fixtures/`) |
| `--salida RUTA` | Excel de salida (por defecto `output/Formulario210_<NIT>.xlsx`) |
| `--sesion RUTA` | Archivo de progreso (por defecto `sessions/sesion.json`) |
| `--anio N` | Año gravable → carga `config/parametros_<N>.yaml` |

## Pruebas

```bash
.venv/bin/python -m pytest tests/ -q     # 59 pruebas, todas en verde
```

Los dos archivos reales de `tests/fixtures/` se usan como fixtures: el caso
"Elizabeth" está **verificado a mano** renglón por renglón en
`tests/test_motor_calculo.py::test_caso_elizabeth_end_to_end`.

## Estructura

```
declaracion-renta/
  config/parametros_2025.yaml   # UVT, topes, tablas — datos, no código
  src/
    parametros.py     # carga de la configuración normativa
    modelos.py        # dataclasses (partidas, datos, liquidación)
    exogena_parser.py # parser tolerante del reporte DIAN
    entrevista.py     # mapeo exógena→datos, preguntas, sesión JSON
    motor_calculo.py  # liquidación renglón por renglón (29–141)
    excel_writer.py   # escribe la plantilla ITGS + hoja Trazabilidad
    cli.py            # interfaz interactiva (rich + questionary)
  tests/              # pytest con los fixtures reales
  run.py
```

La lógica de negocio (parser + motor) está desacoplada de la interfaz: la CLI
solo recorre `SECCIONES` de `entrevista.py`, de modo que una interfaz web
(p. ej. Streamlit) puede reutilizar el mismo motor sin reescribirlo.

## Qué normativa asume (verificar antes de usar en serio)

Todo vive en `config/parametros_2025.yaml`:

- **UVT 2025 = $49.799** (verificar Resolución DIAN).
- Topes de obligados a declarar: 4.500 / 1.400 UVT.
- Límite de rentas exentas y deducciones de la cédula general: menor entre
  **40%** de (ingresos − INCRNGO − devoluciones − costos procedentes) y
  **1.340 UVT**, distribuido en cascada trabajo → honorarios → capital → no
  laborales (como la plantilla ITGS).
- Renta exenta laboral del **25%** (tope 790 UVT/año) calculada
  automáticamente (desactivable con `aplicar_renta_exenta_25=False`).
- Deducción por dependientes: **72 UVT** c/u, máx. 4 — fuera del límite del 40%.
- Deducción **1%** compras con factura electrónica, tope 240 UVT — fuera del 40%.
- Tabla **Art. 241 E.T.** (0/19/28/33/35/37/39%).
- Renta presuntiva **0%** (vigente desde AG 2021).
- Ganancias ocasionales **15%** general / **20%** loterías (Art. 317, sin costos
  ni exención).
- Exenciones de ganancias ocasionales por tipo, en `ganancias_ocasionales.tipos`:
  vivienda del causante **13.000 UVT** (Art. 307 num. 1), otros inmuebles
  heredados **6.500 UVT** (num. 2), porción conyugal **3.250 UVT** (num. 3),
  no legitimarios **20% con techo de 1.625 UVT** (num. 4), seguros de vida
  **3.250 UVT** (Art. 303-1) y venta de vivienda de habitación **5.000 UVT**
  (Art. 311-1, solo si el avalúo catastral no supera 15.000 UVT y el producto se
  deposita en una cuenta AFC). **Verifíquelas**: los numerales del Art. 307 y el
  Art. 311-1 han cambiado en reformas recientes.
- Anticipo Art. 807: 25% / 50% / 75% con el método más favorable.

## Firma y verificación del borrador

El Formulario 210 en PDF sale **rellenable**: cada renglón es un campo AcroForm
editable en cualquier lector, no texto pintado. Los importes viven en los campos
(`R29`, `R115`, …), no en la capa de texto de la página: para leerlos por
programa use `PdfReader(...).get_fields()`, no `extract_text()`.

Cada PDF lleva impreso un **código de verificación** derivado de su SHA-256, que
permite comprobar que el documento entregado es el que se generó
(`src/firma.sello_integridad` / `verificar_sello`).

Opcionalmente se puede firmar con un certificado propio (`.p12`/`.pfx`) mediante
**PAdES** (`POST /api/firmar-pdf`, o `src.firma.firmar_pdf`). El certificado y su
contraseña se procesan en memoria y nunca se escriben a disco ni a los logs.
`src.firma.validar_firma` devuelve `integro=False` si el PDF fue alterado después
de firmarse.

> ⚠️ **La firma NO presenta la declaración ante la DIAN.** El Formulario 210 se
> radica únicamente en el portal MUISCA con la *Firma Electrónica* que la DIAN
> emite al contribuyente y que está atada a su RUT; no existe API pública de
> radicación ni se acepta un PDF firmado localmente. La firma PAdES sirve para
> acreditar la integridad y el origen del **borrador** que un contador entrega a
> su cliente.

## Segundo factor (2FA)

Los usuarios pueden activar **TOTP** (Google Authenticator, Authy, etc.) desde
*Mi cuenta*: `POST /api/configurar-2fa` devuelve el QR, y `POST /api/confirmar-2fa`
lo activa solo tras validar un código real, entregando 10 **códigos de respaldo**
de un solo uso (se guardan hasheados, nunca en claro).

Con 2FA activo, superar el login social **no abre sesión**: el usuario queda en
`uid_pendiente` y solo se promueve a sesión real tras verificar su código en
`/verificar-mfa`. Cinco intentos fallidos bloquean la cuenta 15 minutos.

### Limitaciones conocidas

- **Dividendos**: replica las tablas de la plantilla ITGS (régimen Art. 242
  previo; 1a subcédula 10% > 300 UVT). La **Ley 2277/2022** cambió el
  tratamiento de dividendos 2017+ (tarifa marginal con descuento Art. 254-1);
  si tiene dividendos, revise este punto con un profesional.
- **Componente inflacionario** de rendimientos financieros (R59, Arts. 38,
  40-1 y 41 E.T.): se calcula automáticamente como el **55,43%** (AG 2025,
  proyecto de decreto MinHacienda de abril/2026) de las partidas que la
  exógena marca `R58 | R59`. Solo aplica a personas naturales NO obligadas a
  llevar contabilidad. **Verifique el decreto definitivo** (el de AG 2024 fue
  el Decreto 0771 de julio/2025, 50,88%) y ajuste `componente_inflacionario`
  en el config si cambia. El valor precalculado se puede corregir en la
  entrevista.
- La plantilla ITGS trae internamente **UVT 2023 ($47.065)** en sus fórmulas;
  este programa escribe **valores ya calculados** con la UVT del config, por lo
  que los renglones del formulario quedan consistentes entre sí, pero las hojas
  auxiliares de la plantilla no se recalculan.
- Al reescribir el Excel con openpyxl se pierden dibujos/formas decorativas de
  la plantilla (los estilos y celdas se conservan).
- Renglón 111 suma cédula general + pensiones + subcédulas de dividendos 2017+,
  como la plantilla; el impuesto R116 se calcula sobre general + pensiones.

## Privacidad

Los archivos de entrada contienen datos personales sensibles (NIT, nombres,
patrimonio). **Todo se procesa localmente**; no se hace ninguna llamada a
servicios externos. Los archivos de `sessions/` y `output/` contienen esos
datos: bórrelos o protéjalos cuando termine. Los datos de "Elizabeth" y
"Diana Zorani" en `tests/fixtures/` son solo material de prueba.
