(function () {
  "use strict";

  function cleanCode(raw) {
    return String(raw || "")
      .replace(/[^\dA-Za-z\-_.]/g, "")
      .trim();
  }

  function setStatus(el, text, kind) {
    if (!el) return;
    el.textContent = text || "";
    el.classList.remove("is-ok", "is-err");
    if (kind === "ok") el.classList.add("is-ok");
    if (kind === "err") el.classList.add("is-err");
  }

  function barcodeInput() {
    return (
      document.getElementById("id_barcode") ||
      document.querySelector('input[name="barcode"]')
    );
  }

  function fillBarcode(raw) {
    const code = cleanCode(raw);
    const input = barcodeInput();
    if (!code || !input) return false;
    input.value = code;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.focus();
    return true;
  }

  document.addEventListener("DOMContentLoaded", function () {
    const btnCam = document.getElementById("bc-btn-camera");
    const btnGal = document.getElementById("bc-btn-gallery");
    const fileInput = document.getElementById("bc-file-input");
    const statusEl = document.getElementById("bc-scan-status");
    const modal = document.getElementById("bc-scan-modal");
    const modalStatus = document.getElementById("bc-scan-modal-status");
    const btnClose = document.getElementById("bc-scan-close");
    if (!btnCam || !btnGal) return;

    let scanner = null;
    let running = false;
    let lock = false;

    async function stopScanner() {
      if (!scanner) return;
      try {
        if (running) await scanner.stop();
      } catch (e) {}
      try {
        await scanner.clear();
      } catch (e) {}
      running = false;
      scanner = null;
    }

    async function closeModal() {
      if (modal) modal.hidden = true;
      await stopScanner();
    }

    async function openCamera() {
      if (!modal) return;
      modal.hidden = false;
      setStatus(modalStatus, "Kamera ochilmoqda…");
      if (!window.Html5Qrcode) {
        setStatus(modalStatus, "Skaner kutubxonasi yuklanmadi.", "err");
        return;
      }
      await stopScanner();
      scanner = new window.Html5Qrcode("bc-scan-reader");
      lock = false;
      try {
        await scanner.start(
          { facingMode: "environment" },
          { fps: 12, qrbox: { width: 260, height: 160 }, aspectRatio: 1.5 },
          function (text) {
            if (lock) return;
            lock = true;
            const ok = fillBarcode(text);
            setStatus(
              modalStatus,
              ok ? "Topildi: " + cleanCode(text) : "Kod formati xato",
              ok ? "ok" : "err"
            );
            setStatus(
              statusEl,
              ok ? "Shtrix-kod yozildi: " + cleanCode(text) : "Kod formati xato",
              ok ? "ok" : "err"
            );
            setTimeout(function () {
              closeModal();
              lock = false;
            }, ok ? 350 : 800);
          },
          function () {}
        );
        running = true;
        setStatus(modalStatus, "Kamerani shtrix-kodga qarating");
      } catch (err) {
        setStatus(modalStatus, "Kamera ochilmadi: " + (err && err.message ? err.message : err), "err");
        running = false;
      }
    }

    async function decodeFile(file) {
      if (!file) return "";
      if ("BarcodeDetector" in window) {
        try {
          const bmp = await createImageBitmap(file);
          const detector = new window.BarcodeDetector({
            formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf"],
          });
          const rows = await detector.detect(bmp);
          const val = cleanCode(rows && rows[0] && rows[0].rawValue);
          if (val) return val;
        } catch (e) {}
      }
      if (window.Html5Qrcode) {
        try {
          let el = document.getElementById("bc-file-scan-tmp");
          if (!el) {
            el = document.createElement("div");
            el.id = "bc-file-scan-tmp";
            el.hidden = true;
            document.body.appendChild(el);
          }
          const tmp = new window.Html5Qrcode("bc-file-scan-tmp", false);
          const text = await tmp.scanFile(file, false);
          try {
            await tmp.clear();
          } catch (e) {}
          return cleanCode(text);
        } catch (e) {}
      }
      return "";
    }

    btnCam.addEventListener("click", function (e) {
      e.preventDefault();
      openCamera();
    });
    btnGal.addEventListener("click", function (e) {
      e.preventDefault();
      if (fileInput) fileInput.click();
    });
    btnClose &&
      btnClose.addEventListener("click", function (e) {
        e.preventDefault();
        closeModal();
      });
    fileInput &&
      fileInput.addEventListener("change", async function (e) {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        const code = await decodeFile(file);
        if (code && fillBarcode(code)) {
          setStatus(statusEl, "Shtrix-kod yozildi: " + code, "ok");
          if (modal && !modal.hidden) closeModal();
        } else {
          setStatus(statusEl, "Rasmdan shtrix-kod topilmadi.", "err");
        }
        e.target.value = "";
      });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && modal && !modal.hidden) closeModal();
    });
  });
})();
