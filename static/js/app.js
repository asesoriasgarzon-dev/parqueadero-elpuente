// Auto-uppercase en campos de placa
document.querySelectorAll('input[name="placa"]').forEach(el => {
  el.addEventListener('input', () => el.value = el.value.toUpperCase());
});
