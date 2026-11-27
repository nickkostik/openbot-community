use super::*;

#[test]
fn mixed_layout_keeps_legacy_tuning_and_mode_overrides() {
    let root = tempfile::tempdir().unwrap();
    let mut config = ConfigFile {
        path: root.path().join(FILE),
        mode: None,
        layers: [None; 3],
    };
    assert_eq!(config.report()["mode"], "off");
    assert_eq!(config.report()["source"], "default");
    fs::write(
        &config.path,
        json!({
            "mode":"dry-run",
            "l2":{"maxAdditionalRuns":"0","timeoutMs":6000,"retryBudgetMs":3000},
            "layers":{"l1":{"watchingSilenceThreshold":8},"l3":{"maxRedrivesPerEpoch":2}},
            "extension":{"kept":true},
        })
        .to_string(),
    )
    .unwrap();

    let update = Update::parse(json!({"mode":"enforce","layers":{"l1":false}})).unwrap();
    let result = config.save(update).unwrap();
    assert_eq!(result["mode"], "enforce");
    assert_eq!(result["layers"], json!({"l1":false,"l2":true,"l3":true}));
    assert_eq!(result["maxAdditionalRuns"], 0);
    let raw = config.raw().unwrap().unwrap();
    assert_eq!(raw["extension"]["kept"], true);
    assert_eq!(raw["l1"]["watchingSilenceThreshold"], 8);
    assert_eq!(config.load().l2.timeout, Duration::from_secs(3));

    assert!(Update::parse(json!({"mode":"off","layers":{}})).is_err());
    assert!(Update::parse(json!({"mode":"enforce","layers":{"l3":"false"}})).is_err());
    config
        .save(Update::parse(json!({"mode":"off"})).unwrap())
        .unwrap();
    assert_eq!(config.raw().unwrap().unwrap()["l1"], raw["l1"]);
    config.mode = Some(Mode::DryRun);
    config.layers[0] = env_bool(" Yes ");
    let result = config.report();
    assert_eq!(result["mode"], "dry-run");
    assert_eq!(result["source"], "env");
    assert_eq!(result["envOverride"], true);
    assert_eq!(result["layers"]["l1"], true);
}

#[test]
fn default_enabled_settings_write_all_layers_and_bad_files_fail_open_to_off() {
    let root = tempfile::tempdir().unwrap();
    let config = ConfigFile {
        path: root.path().join("test/explicit.json"),
        mode: None,
        layers: [None; 3],
    };
    let report = config
        .save(Update::parse(json!({"mode":"enforce","layers":null})).unwrap())
        .unwrap();
    assert_eq!(report["layers"], json!({"l1":true,"l2":true,"l3":true}));
    assert_eq!(report["path"], config.path.to_str().unwrap());
    fs::write(&config.path, "malformed").unwrap();
    assert_eq!(config.load().mode, Mode::Off);
    assert_eq!(config.report()["source"], "default");
    assert_eq!(config.report()["exists"], true);
}
