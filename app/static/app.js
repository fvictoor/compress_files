(() => {
  "use strict";

  const form = document.getElementById("compress-form");
  const fileInput = document.getElementById("files");
  const dropZone = document.getElementById("drop-zone");
  const fileList = document.getElementById("file-list");
  const formatSelect = document.getElementById("format");
  const formatDesc = document.getElementById("format-desc");
  const levelInput = document.getElementById("level");
  const levelValue = document.getElementById("level-value");
  const levelDesc = document.getElementById("level-desc");
  const submitBtn = document.getElementById("submit");
  const progressWrap = document.getElementById("progress-wrap");
  const progress = document.getElementById("progress");
  const statusEl = document.getElementById("status");
  const errorEl = document.getElementById("error");

  let config = { formats: [], maxUploadMb: 0, maxFiles: 0 };
  let selectedFiles = [];

  const formatBytes = (n) => {
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
  };

  const setStatus = (msg) => { statusEl.textContent = msg; };
  const setError = (msg) => { errorEl.textContent = msg; };

  function updateLevelHint() {
    const v = Number(levelInput.value);
    levelValue.textContent = v;
    levelDesc.textContent =
      v <= 3 ? "Baixa compressão: imagens em alta qualidade (ideal para impressão)." :
      v <= 6 ? "Compressão média: boa qualidade para tela e impressão simples." :
      v <= 8 ? "Alta compressão: ótima para enviar por e-mail e WhatsApp." :
               "Compressão extrema: menor tamanho possível, imagens visivelmente mais simples.";
  }

  function updateFormatHint() {
    const f = config.formats.find((x) => x.key === formatSelect.value);
    formatDesc.textContent = f ? f.description : "";
  }

  function setFiles(files) {
    selectedFiles = Array.from(files);
    fileList.replaceChildren(...selectedFiles.map((f) => {
      const li = document.createElement("li");
      const name = document.createElement("span");
      const size = document.createElement("span");
      name.textContent = f.name;
      name.title = f.name;
      size.textContent = formatBytes(f.size);
      li.append(name, size);
      return li;
    }));
    setError("");
    setStatus("");
  }

  async function loadConfig() {
    try {
      const res = await fetch("/api/config");
      config = await res.json();
      formatSelect.replaceChildren(...config.formats.map((f) => new Option(f.label, f.key)));
      levelInput.min = config.minLevel;
      levelInput.max = config.maxLevel;
      updateFormatHint();
    } catch {
      setError("Não foi possível carregar a configuração do servidor.");
    }
  }

  function readError(xhr) {
    return new Promise((resolve) => {
      const fallback = `Erro ${xhr.status} ao comprimir.`;
      if (!(xhr.response instanceof Blob)) return resolve(fallback);
      xhr.response.text().then((txt) => {
        try {
          const detail = JSON.parse(txt).detail;
          resolve(typeof detail === "string" ? detail : fallback);
        } catch { resolve(fallback); }
      }, () => resolve(fallback));
    });
  }

  function filenameFrom(xhr) {
    const cd = xhr.getResponseHeader("Content-Disposition") || "";
    const utf = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf) return decodeURIComponent(utf[1]);
    const plain = cd.match(/filename="?([^";]+)"?/i);
    return plain ? plain[1] : "arquivo_comprimido";
  }

  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  function onSubmit(event) {
    event.preventDefault();
    setError("");
    setStatus("");

    if (selectedFiles.length === 0) return setError("Selecione ao menos um arquivo.");
    if (config.maxFiles && selectedFiles.length > config.maxFiles) {
      return setError(`Máximo de ${config.maxFiles} arquivos por envio.`);
    }
    const total = selectedFiles.reduce((s, f) => s + f.size, 0);
    if (config.maxUploadMb && total > config.maxUploadMb * 1024 * 1024) {
      return setError(`O total (${formatBytes(total)}) excede o limite de ${config.maxUploadMb} MB.`);
    }

    const data = new FormData();
    selectedFiles.forEach((f) => data.append("files", f, f.name));
    data.append("format", formatSelect.value);
    data.append("level", levelInput.value);
    data.append("optimize_media", document.getElementById("optimize").checked ? "true" : "false");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/compress");
    xhr.responseType = "blob";

    submitBtn.disabled = true;
    progressWrap.hidden = false;
    progress.value = 0;
    setStatus("Enviando arquivos…");

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        progress.value = pct;
        setStatus(`Enviando arquivos… ${pct}%`);
      }
    };
    xhr.upload.onload = () => {
      progress.removeAttribute("value"); // barra indeterminada enquanto comprime
      setStatus("Comprimindo no servidor…");
    };

    xhr.onload = async () => {
      submitBtn.disabled = false;
      progressWrap.hidden = true;
      if (xhr.status !== 200) {
        setStatus("");
        return setError(await readError(xhr));
      }
      const original = Number(xhr.getResponseHeader("X-Original-Size")) || total;
      const compressed = Number(xhr.getResponseHeader("X-Compressed-Size")) || xhr.response.size;
      const saved = original > 0 ? (1 - compressed / original) * 100 : 0;
      download(xhr.response, filenameFrom(xhr));
      const requestId = xhr.getResponseHeader("X-Request-Id");
      setStatus(
        `Pronto: ${formatBytes(original)} → ${formatBytes(compressed)} ` +
        (saved > 0 ? `(${saved.toFixed(1)}% menor).` : "(o arquivo já estava comprimido).") +
        (requestId ? ` ID do envio: ${requestId.slice(0, 8)}` : "")
      );
    };
    xhr.onerror = () => {
      submitBtn.disabled = false;
      progressWrap.hidden = true;
      setStatus("");
      setError("Falha de conexão com o servidor.");
    };

    xhr.send(data);
  }

  fileInput.addEventListener("change", () => setFiles(fileInput.files));
  ["dragenter", "dragover"].forEach((ev) =>
    dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.remove("dragover"); }));
  dropZone.addEventListener("drop", (e) => {
    if (e.dataTransfer?.files?.length) {
      fileInput.files = e.dataTransfer.files;
      setFiles(e.dataTransfer.files);
    }
  });
  levelInput.addEventListener("input", updateLevelHint);
  formatSelect.addEventListener("change", updateFormatHint);
  form.addEventListener("submit", onSubmit);

  updateLevelHint();
  loadConfig();
})();
