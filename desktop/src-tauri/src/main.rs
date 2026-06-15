fn main() {
    if let Err(error) = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .run(tauri::generate_context!())
    {
        eprintln!("error while running xbot desktop app: {error}");
        std::process::exit(1);
    }
}
