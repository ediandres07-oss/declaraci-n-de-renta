(()=>{
const p=n=>String(n).padStart(2,'0'),h=new Date(),a=new Date(h.getFullYear(),h.getMonth()-1,1),b=new Date(h.getFullYear(),h.getMonth(),0);
const iso=d=>d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate()),ok=s=>/^\d{4}-\d{2}-\d{2}$/.test(s||'');
let ini=prompt('FECHAS DIAN · Tributando\nFecha INICIAL (AAAA-MM-DD):',iso(a));if(ini===null)return;ini=ini.trim();
let fin=prompt('Fecha FINAL (AAAA-MM-DD):',ini.slice(0,7)===iso(a).slice(0,7)?iso(b):ini);if(fin===null)return;fin=fin.trim();
if(!ok(ini)||!ok(fin)){alert('Escribe las fechas así: 2026-10-01');return}
if(ini>fin){const t=ini;ini=fin;fin=t}
const fmt=(s,ej)=>{const[y,m,d]=s.split('-');if(/^\d{2}\/\d{2}\/\d{4}/.test(ej||''))return d+'/'+m+'/'+y;if(/^\d{4}\/\d{2}\/\d{2}/.test(ej||''))return y+'/'+m+'/'+d;if(/^\d{2}-\d{2}-\d{4}/.test(ej||''))return d+'-'+m+'-'+y;return s};
const poner=(el,v)=>{el.removeAttribute('readonly');el.removeAttribute('disabled');el.disabled=false;el.readOnly=false;
 Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,v);
 ['input','change','blur'].forEach(t=>el.dispatchEvent(new Event(t,{bubbles:true})))};
let n=0;const $=window.jQuery;
document.querySelectorAll('input').forEach(el=>{
 const k=((el.name||'')+' '+(el.id||'')+' '+(el.placeholder||'')+' '+(el.className||'')).toLowerCase();
 if(!/date|fecha|range|rango|desde|hasta/.test(k))return;
 const drp=$&&$(el).data&&$(el).data('daterangepicker');
 if(drp&&window.moment){drp.setStartDate(moment(ini));drp.setEndDate(moment(fin));try{drp.clickApply()}catch(e){$(el).trigger('apply.daterangepicker',drp)}n++;return}
 const v=el.value||'';
 if(/range|rango/.test(k)){poner(el,fmt(ini,v)+' - '+fmt(fin,(v.split(' - ')[1])||v));n++}
 else if(/start|ini|desde|initial|from/.test(k)){poner(el,fmt(ini,v));n++}
 else if(/end|fin|hasta|final|to\b/.test(k)){poner(el,fmt(fin,v));n++}
});
alert(n?'Listo: puse del '+ini+' al '+fin+'.\nAhora dale «Exportar Excel».\n\n¿Quieres los XML y no solo el Excel? tributando.co':'No encontré los campos de fecha.\nAbre primero «Descargar listados» en el portal de la DIAN y vuelve a tocar FECHAS DIAN.');
})();
