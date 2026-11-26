mod auth;
mod bot_models;
mod catalog;
mod discovery;
mod hop;
mod lease;
mod reasoning;
mod routing;
mod server;
mod wire;

use std::{
    io::{self, Write},
    net::Ipv4Addr,
    path::PathBuf,
};

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(
    version,
    about = "OpenBot Rust candidate for bot settings, provider accounts, and inference"
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
    /// Enable provider inference in a disposable Custom state directory.
    #[arg(long)]
    enable_hop: bool,
    /// Enable local OpenAI sign-in, credential writes, and renewal in disposable state.
    #[arg(long)]
    enable_accounts: bool,
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
        /// Encode a canonical request fixture for one selected model.
        #[arg(long)]
        request: Option<PathBuf>,
    },
}

fn main() -> io::Result<()> {
    let args = Args::parse();
    if let Some(Command::Resolve {
        model,
        bot,
        credential,
        request,
    }) = args.command
    {
        let state = server::State::new(args.sand_data, false)?;
        let routes = state.resolve_routes(&model, bot.as_deref(), credential)?;
        let mut out = io::stdout().lock();
        if let Some(path) = request {
            let [route] = routes.as_slice() else {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "a request fixture requires exactly one model",
                ));
            };
            let request =
                server::read_json(&path, 16 * 1024 * 1024)?.ok_or(io::ErrorKind::NotFound)?;
            let body = route
                .encode_request(request, credential)
                .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
            serde_json::to_writer(&mut out, &serde_json::json!({"route": route, "body": body}))?;
        } else {
            serde_json::to_writer(&mut out, &routes)?;
        }
        writeln!(out)?;
        return Ok(());
    }
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(serve(
            args.sand_data,
            args.port,
            args.write_bot_settings,
            args.enable_hop,
            args.enable_accounts,
        ))
}

async fn serve(
    sand_data: PathBuf,
    port: u16,
    writable: bool,
    enable_hop: bool,
    enable_accounts: bool,
) -> io::Result<()> {
    let state = server::State::new(sand_data, writable)?;
    let listener = tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST, port)).await?;
    let address = listener.local_addr()?;
    let authority = if address.port() == 80 {
        address.ip().to_string()
    } else {
        address.to_string()
    };
    let credentials = (enable_hop || enable_accounts)
        .then(|| auth::CredentialStore::new(state.root(), enable_accounts))
        .transpose()?;
    let hop = credentials
        .clone()
        .filter(|_| enable_hop)
        .map(|store| hop::Hop::new(state.clone(), store))
        .transpose()?;
    let accounts = credentials
        .clone()
        .filter(|_| enable_accounts)
        .map(|store| auth::Accounts::new(store, &authority))
        .transpose()?;
    let discovery = credentials
        .clone()
        .map(|store| discovery::Discovery::new(state.clone(), store))
        .transpose()?;
    println!("OpenBot Rust listening on http://{authority}");
    let result = axum::serve(
        listener,
        server::router(state, authority, hop, accounts, discovery),
    )
    .with_graceful_shutdown(shutdown())
    .await;
    if let Some(credentials) = credentials {
        credentials.wait_for_renewals().await;
    }
    result
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
