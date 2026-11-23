// Selector de varios valores con búsqueda (templates/componentes/_selector_multiple.html).
// Las casillas son el estado: el formulario envía lo que esté marcado, con o sin este script.
document.addEventListener("alpine:init", () => {
  const normalizar = (texto) =>
    texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();

  window.Alpine.data("selectorMultiple", () => ({
    q: "",
    elegidos: [],

    init() {
      this.leer();
    },
    casillas() {
      return [...this.$root.querySelectorAll('input[type="checkbox"]')];
    },
    texto(casilla) {
      return casilla.labels[0] ? casilla.labels[0].textContent.trim() : casilla.value;
    },
    leer() {
      this.elegidos = this.casillas()
        .filter((c) => c.checked)
        .map((c) => ({ valor: c.value, texto: this.texto(c), desactivado: c.disabled }));
    },
    coincide(fila) {
      return !this.q || normalizar(fila.textContent).includes(normalizar(this.q));
    },
    visibles() {
      return this.casillas().filter((c) => this.coincide(c.closest(".form-check")));
    },
    get hayVisibles() {
      return this.visibles().length > 0;
    },
    enfocar(indice) {
      const casilla = this.visibles()[indice];
      if (casilla) casilla.focus();
    },
    mover(paso) {
      const visibles = this.visibles();
      const actual = visibles.indexOf(document.activeElement);
      if (actual === -1) return;
      const siguiente = visibles[actual + paso];
      if (siguiente) siguiente.focus();
      else if (paso < 0) this.$refs.buscar.focus();
    },
    marcarPrimera() {
      const casilla = this.visibles()[0];
      if (casilla && !casilla.disabled) casilla.click(); // dispara «change» → leer()
    },
    quitar(valor) {
      const casilla = this.casillas().find((c) => c.value === valor);
      if (casilla && !casilla.disabled) {
        casilla.checked = false;
        this.leer();
        casilla.focus();
      }
    },
  }));
});
