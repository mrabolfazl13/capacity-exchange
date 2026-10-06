// Native host for the desktop client.
//
// There are deliberately no Tauri commands here: the webview talks to the backend over
// plain HTTP (§CONTRACTS §2) and holds its own session, so nothing in the app's feature
// set needs a native call. Commands get added when a feature genuinely requires the OS —
// and until then an empty command surface is the honest shape, not a missing one.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running the Capacity Exchange desktop host");
}
