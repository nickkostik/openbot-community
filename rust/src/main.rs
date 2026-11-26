mod bot_models;
mod catalog;
mod server;

use std::{io, net::Ipv4Addr, path::PathBuf};

use clap::Parser;

#[derive(Parser)]
#[command(
    version,
    about = "OpenBot Rust candidate for native bot model settings"
)]
struct Args {
    /// Directory containing the existing OpenBot plan and bot settings.
    #[arg(long)]
    sand_data: PathBuf,
    /// Loopback port. Use 0 for an ephemeral comparison listener.
    #[arg(long, default_value_t = 0)]
    port: u16,
    /// Permit writes when testing against a separate copy of the state.
    #[arg(long)]
    write_bot_settings: bool,
}

#[tokio::main]
async fn main() -> io::Result<()> {
    let args = Args::parse();
    let state = server::State::new(args.sand_data, args.write_bot_settings)?;
    let listener = tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST, args.port)).await?;
    let address = listener.local_addr()?;
    let authority = if address.port() == 80 {
        address.ip().to_string()
    } else {
        address.to_string()
    };
    println!("OpenBot Rust listening on http://{authority}");
    axum::serve(listener, server::router(state, authority))
        .with_graceful_shutdown(shutdown())
        .await
}

async fn shutdown() {
    #[cfg(unix)]
    if let Ok(mut terminate) =
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
    {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {},
            _ = terminate.recv() => {},
        }
        return;
    }
    let _ = tokio::signal::ctrl_c().await;
}
