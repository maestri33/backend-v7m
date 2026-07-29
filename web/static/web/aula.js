/* LMS · aula obrigatória. O vídeo é o gate: a resposta só libera 100% assistida.
   Composer estilo mensageiro (mic sem texto, enviar com texto) + revisão antes do envio.
   O envio NÃO espera correção: manda e o promotor já segue — a IA corrige em segundo plano. */
function aula(jaVisto) {
  return {
    tocando: false, visto: !!jaVisto, pos: 0, dur: 30, pct: jaVisto ? 100 : 0,
    texto: '', rec: 'idle', recSecs: 0, revisar: null,
    falando: false, ouvindo: false, pbSecs: 0, enviando: false,
    _mr: null, _chunks: [], _blob: null, _recI: null, _pbI: null, _fake: null, _url: null, _au: null,

    init() {
      this.$watch('revisar', (v) => { if (!v) this.paraMidia(); });
      window.addEventListener('beforeunload', () => this.paraMidia());
    },
    fmt(s) { s = Math.max(0, Math.round(s || 0)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); },
    cresce(el) { el.style.height = '44px'; el.style.height = Math.min(130, el.scrollHeight) + 'px'; },

    /* ---- vídeo ---- */
    play() {
      if (this.visto) return;
      var v = this.$refs.v;
      if (v) {
        if (v.paused) { v.play().catch(() => {}); this.tocando = true; }
        else { v.pause(); this.tocando = false; }
        return;
      }
      // aula sem vídeo publicado: barra de leitura, mesmo gate
      this.tocando = !this.tocando;
      if (this._fake) clearInterval(this._fake);
      if (!this.tocando) return;
      // aula só de texto: mesma cadência do protótipo (1.6% a cada 120ms ≈ 7,5s de leitura)
      this.dur = this.dur || 30;
      this._fake = setInterval(() => {
        this.pct = Math.min(100, this.pct + 1.6);
        this.pos = (this.pct / 100) * this.dur;
        if (this.pct >= 100) { clearInterval(this._fake); this.fim(); }
      }, 120);
    },
    tick() {
      var v = this.$refs.v; if (!v || !v.duration) return;
      this.pos = v.currentTime; this.dur = v.duration;
      this.pct = Math.min(100, (v.currentTime / v.duration) * 100);
      this.tocando = !v.paused;
    },
    fim() { this.tocando = false; this.visto = true; this.pct = 100; this.pos = this.dur; },

    /* ---- composer ---- */
    principal() {
      if (!this.visto) return;
      if (this.texto.trim().length) { this.revisar = 'texto'; return; }
      this.gravar();
    },
    async gravar() {
      try {
        var st = await navigator.mediaDevices.getUserMedia({ audio: true });
        var mime = ['audio/webm', 'audio/mp4', 'audio/ogg'].find(function (m) {
          return window.MediaRecorder && MediaRecorder.isTypeSupported(m);
        }) || '';
        this._chunks = [];
        this._mr = new MediaRecorder(st, mime ? { mimeType: mime } : undefined);
        this._mr.ondataavailable = (e) => { if (e.data && e.data.size) this._chunks.push(e.data); };
        this._mr.onstop = () => {
          st.getTracks().forEach(function (t) { t.stop(); });
          this._blob = new Blob(this._chunks, { type: this._mr.mimeType || 'audio/webm' });
        };
        this._mr.start();
        this.rec = 'rec'; this.recSecs = 0;
        if (this._recI) clearInterval(this._recI);
        this._recI = setInterval(() => { this.recSecs += 1; }, 1000);
      } catch (e) {
        // sem permissão de microfone: o texto continua sendo o caminho
        this.rec = 'idle';
        alert('Não consegui acessar o microfone. Escreva a resposta que também vale.');
      }
    },
    cancelaRec() {
      if (this._recI) clearInterval(this._recI);
      try { if (this._mr && this._mr.state !== 'inactive') this._mr.stop(); } catch (e) {}
      this._blob = null; this.rec = 'idle'; this.recSecs = 0;
    },
    paraRec() {
      if (this._recI) clearInterval(this._recI);
      try { if (this._mr && this._mr.state !== 'inactive') this._mr.stop(); } catch (e) {}
      this.rec = 'done'; this.revisar = 'audio';
    },

    /* ---- revisão ---- */
    paraMidia() {
      try { window.speechSynthesis.cancel(); } catch (e) {}
      if (this._pbI) clearInterval(this._pbI);
      if (this._au) { try { this._au.pause(); } catch (e) {} }
      this.falando = false; this.ouvindo = false; this.pbSecs = 0;
    },
    tts() {
      if (this.falando) { this.paraMidia(); return; }
      try {
        window.speechSynthesis.cancel();
        var u = new SpeechSynthesisUtterance(this.texto);
        u.lang = 'pt-BR';
        u.onend = () => { this.falando = false; };
        this.falando = true;
        window.speechSynthesis.speak(u);
      } catch (e) { this.falando = false; }
    },
    playback() {
      if (this.ouvindo) { this.paraMidia(); return; }
      if (!this._blob) return;
      if (!this._url) this._url = URL.createObjectURL(this._blob);
      if (!this._au) this._au = new Audio(this._url);
      this._au.currentTime = 0;
      this._au.onended = () => { this.paraMidia(); };
      this._au.play().catch(() => {});
      this.ouvindo = true; this.pbSecs = 0;
      if (this._pbI) clearInterval(this._pbI);
      this._pbI = setInterval(() => {
        this.pbSecs += 1;
        if (this.pbSecs >= this.recSecs) this.paraMidia();
      }, 1000);
    },
    corrigir() {
      var eraAudio = this.revisar === 'audio';
      this.paraMidia();
      this.revisar = null;
      if (eraAudio) { this._blob = null; this._url = null; this._au = null; this.rec = 'idle'; this.recSecs = 0; }
    },

    /* ---- envio: manda e sai. Correção é assíncrona. ---- */
    confirmar() {
      if (this.enviando) return;
      this.enviando = true;
      this.paraMidia();
      if (this.revisar === 'texto') { this.revisar = null; htmx.trigger('#f-txt', 'submit'); return; }
      this.revisar = null;
      var fd = new FormData();
      fd.append('material', document.querySelector('#f-txt [name=material]').value);
      fd.append('audio', this._blob, 'resposta.webm');
      fd.append('csrfmiddlewaretoken', document.querySelector('[name=csrfmiddlewaretoken]').value);
      fetch(document.getElementById('f-txt').dataset.audioUrl, { method: 'POST', body: fd, credentials: 'same-origin' })
        .then((r) => { window.location.href = r.headers.get('HX-Redirect') || '/app/painel'; })
        .catch(() => { this.enviando = false; alert('Não consegui enviar o áudio. Tenta de novo.'); });
    },
  };
}
