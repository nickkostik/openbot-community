mod aggregate;
mod auth;
mod bot_models;
mod catalog;
mod compaction;
mod delivery;
mod discovery;
mod hop;
mod host;
mod ingress;
mod jwt;
mod lease;
mod logs;
mod panel;
mod reasoning;
mod routing;
mod server;
mod service;
mod skills;
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
    /// Enable manual and scheduled catalog refresh writes in disposable state.
    #[arg(long)]
    enable_model_refresh: bool,
    /// Record requests and maintain the log archive in disposable state.
    #[arg(long)]
    enable_request_logs: bool,
    /// Compact long Custom histories in disposable state.
    #[arg(long, requires = "enable_hop")]
    enable_compaction: bool,
    /// Serve the built OpenBot web assets from this directory.
    #[arg(long)]
    ui_dir: Option<PathBuf>,
    /// Publish ownership after binding the configured loopback port.
    #[arg(long, requires = "ui_dir")]
    managed_service: bool,
    /// Observe this Computer's Node host entrypoint. Defaults to OPENBOT_HOST_MAIN.
    #[arg(long)]
    host_main: Option<PathBuf>,
    /// Manage the host using this installed runtime.cjs and a validated reload.
    #[arg(long, requires_all = ["host_main", "managed_service", "enable_hop", "enable_accounts"])]
    host_adapter: Option<PathBuf>,
    /// Node executable used to validate staged host source.
    #[arg(long, default_value = "node")]
    host_node: PathBuf,
    /// Cloudflare Access applications permitted to use the panel.
    #[arg(long, requires = "ui_dir")]
    access_config: Option<PathBuf>,
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
    #[cfg(target_os = "linux")]
    /// Install or roll back the runtime of an existing OpenBot user service.
    Service {
        #[arg(long)]
        unit: String,
        #[command(subcommand)]
        action: service::deploy::Action,
    },
}

fn main() -> io::Result<()> {
    let args = Args::parse();
    #[cfg(target_os = "linux")]
    if let Some(Command::Service { unit, action }) = &args.command {
        return tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?
            .block_on(service::deploy::run(
                &args.sand_data,
                args.port,
                unit,
                action,
            ));
    }
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
        .block_on(serve(args))
}

async fn serve(args: Args) -> io::Result<()> {
    let Args {
        sand_data,
        port,
        write_bot_settings,
        enable_hop,
        enable_accounts,
        enable_model_refresh,
        enable_request_logs,
        enable_compaction,
        ui_dir,
        managed_service,
        host_main,
        host_adapter,
        host_node,
        access_config,
        ..
    } = args;
    if managed_service && port == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "a managed service requires a fixed loopback port",
        ));
    }
    let listener = tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST, port)).await?;
    let state =
        server::State::with_catalog_writer(sand_data, write_bot_settings, enable_model_refresh)?;
    let address = listener.local_addr()?;
    let authority = if address.port() == 80 {
        address.ip().to_string()
    } else {
        address.to_string()
    };
    let request_policy = ingress::Policy::new(authority.clone(), access_config.as_deref())?;
    let status = panel::ListenerStatus {
        port: address.port(),
        serves_ui: ui_dir.is_some(),
        serves_hop: enable_hop,
        public_access: request_policy.public_url().map(str::to_owned),
    };
    let host = host_main
        .or_else(|| {
            std::env::var_os("OPENBOT_HOST_MAIN")
                .filter(|path| !path.is_empty())
                .map(PathBuf::from)
        })
        .map(|path| host::Host::new(state.root(), path, host_adapter, host_node))
        .transpose()?;
    let credentials = (enable_hop || enable_accounts || enable_model_refresh || status.serves_ui)
        .then(|| auth::CredentialStore::new(state.root(), enable_accounts || enable_model_refresh))
        .transpose()?;
    let logs = logs::Logs::new(state.root(), enable_request_logs);
    let compactor = enable_compaction
        .then(|| compaction::Compactor::new(state.root()))
        .transpose()?;
    let hop = credentials
        .clone()
        .filter(|_| enable_hop)
        .map(|store| {
            hop::Hop::new(state.clone(), store).map(|hop| {
                hop.with_logs(logs.clone())
                    .with_compactor(compactor.clone())
                    .with_host(host.clone())
            })
        })
        .transpose()?;
    let accounts = credentials
        .clone()
        .filter(|_| enable_accounts)
        .map(|store| auth::Accounts::new(store, &authority, status.clone(), host.clone()))
        .transpose()?;
    let discovery = credentials
        .clone()
        .filter(|_| enable_hop || enable_accounts || enable_model_refresh)
        .map(|store| discovery::Discovery::new(state.clone(), store))
        .transpose()?;
    let panel = ui_dir
        .zip(credentials.clone())
        .map(|(directory, store)| panel::routes(directory, store, status.clone(), host.clone()))
        .transpose()?;
    let refresh = discovery
        .clone()
        .zip(credentials.clone())
        .filter(|_| enable_model_refresh);
    let catalog = (status.serves_ui || enable_model_refresh)
        .then(|| aggregate::CatalogService::new(state.clone(), refresh))
        .transpose()?;
    let skills = status
        .serves_ui
        .then(|| skills::Skills::new(state.root(), write_bot_settings, hop::version()))
        .transpose()?;
    let _owner = managed_service
        .then(|| service::PublishedOwner::publish(state.root()))
        .transpose()?;
    let host_watch = host.as_ref().and_then(host::Host::watch);
    if let Some(catalog) = &catalog {
        catalog.start();
    }
    let services = [
        hop.map(hop::Hop::routes),
        accounts.map(auth::Accounts::routes),
        discovery.map(discovery::Discovery::routes),
        panel,
        catalog.as_ref().map(aggregate::CatalogService::routes),
        (status.serves_ui || enable_request_logs).then(|| logs.clone().routes()),
        skills.map(skills::Skills::routes),
        status
            .serves_ui
            .then(|| delivery::config::routes(state.clone(), write_bot_settings)),
    ];
    println!("OpenBot Rust listening on http://{authority}");
    let draining = catalog.clone();
    let stop_compactor = compactor.clone();
    let log_cleanup = logs.schedule_cleanup();
    let result = axum::serve(
        listener,
        server::router(state, request_policy, services.into_iter().flatten()),
    )
    .with_graceful_shutdown(async move {
        shutdown().await;
        if let Some(task) = host_watch {
            task.abort();
            let _ = task.await;
        }
        if let Some(task) = log_cleanup {
            task.abort();
            let _ = task.await;
        }
        if let Some(catalog) = draining {
            catalog.stop();
        }
        if let Some(compactor) = stop_compactor {
            compactor.stop();
        }
    })
    .await;
    if let Some(catalog) = catalog {
        catalog.stop();
        catalog.wait_idle().await;
    }
    if let Some(credentials) = credentials {
        credentials.wait_for_renewals().await;
    }
    logs.wait_for_writes().await;
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
