frappe.pages["scan-qrcode"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Scan QRCode",
		single_column: true,
	});
	page.main.html(frappe.render_template("scan_qrcode", {}));
	setTimeout(function () {
		startScanner();
	}, 1000);
};

var lastResult,
	countResults = 0;

function startScanner() {
	var resultContainer = document.getElementById("qr-reader-results");

	var html5QrcodeScanner = new Html5QrcodeScanner("qr-reader", {
		fps: 10,
		qrbox: 250,
	});
	html5QrcodeScanner.render(onScanSuccess);
}

function onScanSuccess(decodedText, decodedResult) {
	if (decodedText !== lastResult) {
		++countResults;
		lastResult = decodedText;
		// Handle on success condition with the decoded message.
		console.log(`Scan result ${decodedText}`, decodedResult);
		sendApiCall(decodedText);
	}
}

function sendApiCall(decodedText) {
	frappe.call({
		method: "csf_tz.csf_tz.page.scan_qrcode.scan_qrcode.add_biometric_log",
		args: {
			data: decodedText,
		},
		callback: function (r) {
			console.log(r);
		},
	});
}
