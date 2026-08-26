const SCANNER_LIBRARY_URL = "https://unpkg.com/html5-qrcode";

frappe.pages["scan-qrcode"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: "Scan QRCode",
    single_column: true,
  });
  page.main.html(frappe.render_template("scan_qrcode", {}));
  loadScannerLibrary()
    .then(startScanner)
    .catch(() =>
      frappe.msgprint(__("Could not load the QR scanner library (html5-qrcode). Check the internet connection."))
    );
};

let lastResult;

function loadScannerLibrary() {
  if (window.Html5QrcodeScanner) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCANNER_LIBRARY_URL;
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

function startScanner() {
  const scanner = new Html5QrcodeScanner("qr-reader", { fps: 10, qrbox: 250 });
  scanner.render(onScanSuccess);
}

function onScanSuccess(decodedText) {
  if (decodedText === lastResult) {
    return;
  }
  lastResult = decodedText;
  frappe.call({
    method: "csf_tz.csf_tz.page.scan_qrcode.scan_qrcode.add_biometric_log",
    args: { data: decodedText },
  });
}
