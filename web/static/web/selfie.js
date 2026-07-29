/* Selfie: acordo → câmera → conferência. A câmera é de verdade (getUserMedia); quando o
   navegador não dá acesso, cai no seletor nativo do celular em vez de travar a etapa. */
function selfie() {
  return {
    fase: 'acordo', leu: false, camOk: false, pronta: false, erroCam: '', url: null, enviando: false, _stream: null, _blob: null,

    init() {
      window.addEventListener('beforeunload', () => this.fecha());
      // acordo curto que já nasce lido: sem barra de rolagem não há como "rolar até o fim"
      this.$nextTick(() => {
        const el = this.$el.querySelector('.acordo');
        if (el && el.scrollHeight <= el.clientHeight + 4) this.leu = true;
      });
    },
    fecha() {
      if (this._stream) { this._stream.getTracks().forEach((t) => t.stop()); this._stream = null; }
    },
    async abreCamera() {
      this.camOk = false; this.pronta = false;
      this.fecha();
      try {
        this._stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
        // o <video> se liga sozinho no x-init quando o template renderiza: esperar o $refs
        // aqui é corrida com o scheduler do Alpine — foi o que deixava o visor preto.
        this.camOk = true;
      } catch (e) {
        this.camOk = false;
        this.erroCam = String((e && e.message) || e);
      }
    },
    liga(v) {
      if (!this._stream) return;
      v.srcObject = this._stream;
      v.play().catch(() => {});
    },
    captura() {
      const v = this.$refs.cam;
      if (!v || !v.videoWidth) return;
      // recorte central 3:4 — o mesmo enquadramento que a pessoa viu no visor
      const alvo = 3 / 4;
      let sw = v.videoWidth, sh = Math.round(sw / alvo);
      if (sh > v.videoHeight) { sh = v.videoHeight; sw = Math.round(sh * alvo); }
      const sx = (v.videoWidth - sw) / 2, sy = (v.videoHeight - sh) / 2;
      const c = document.createElement('canvas');
      c.width = 720; c.height = 960;
      const ctx = c.getContext('2d');
      ctx.translate(c.width, 0); ctx.scale(-1, 1); // espelha: sai como a pessoa se vê
      ctx.drawImage(v, sx, sy, sw, sh, 0, 0, c.width, c.height);
      c.toBlob((b) => {
        if (!b) return;
        this._blob = b;
        if (this.url) URL.revokeObjectURL(this.url);
        this.url = URL.createObjectURL(b);
        this.fecha();
        this.fase = 'confere';
      }, 'image/jpeg', 0.9);
    },
    doArquivo(ev) {
      const f = ev.target.files && ev.target.files[0];
      if (!f) return;
      this._blob = f;
      if (this.url) URL.revokeObjectURL(this.url);
      this.url = URL.createObjectURL(f);
      this.fase = 'confere';
    },
    envia() {
      if (this.enviando || !this._blob) return;
      this.enviando = true;
      // entrega os bytes ao input do form e deixa o htmx enviar: CSRF, indicador e swap
      // continuam sendo dele — nada de fetch paralelo reimplementando o que já funciona.
      const inp = document.getElementById('selfie-file');
      const dt = new DataTransfer();
      dt.items.add(new File([this._blob], 'selfie.jpg', { type: this._blob.type || 'image/jpeg' }));
      inp.files = dt.files;
      document.getElementById('selfie-form').requestSubmit();
    },
  };
}
