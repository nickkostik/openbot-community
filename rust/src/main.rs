mod bot_models;
mod catalog;
mod routing;
mod server;

use std::{
    io::{self, Write},
    net::Ipv4Addr,
    path::PathBuf,
};

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(
    version,
    about = "OpenBot Rust candidate for bot model settings and route resolution"
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
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// Preview the selected upstream without sending a request.
    Resolve {
        #[arg(long, required = true)]
        model: Vec<String>,
        #[arg(long)]
        bot: Option<String>,
        #[arg(long, value_enum, default_value_t)]
        credential: routing::CredentialKind,
    },
}

fn main() -> io::Result<()> {
    let args = Args::parse();
    if let Some(Command::Resolve {
        model,
        bot,
        credential,
    }) = args.command
    {
        let state = server::State::new(args.sand_data, false)?;
        let routes = state.resolve_routes(&model, bot.as_deref(), credential)?;
        let mut out = io::stdout().lock();
        serde_json::to_writer(&mut out, &routes)?;
        writeln!(out)?;
        return Ok(());
    }
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(serve(args.sand_data, args.port, args.write_bot_settings))
}

async fn serve(sand_data: PathBuf, port: u16, writable: bool) -> io::Result<()> {
    let state = server::State::new(sand_data, writable)?;
    let listener = tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST, port)).await?;
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
