# Quant Archetype

For quantitative trading, backtesting, and strategy research projects.

## Target Projects

- Trading systems with backtesting
- Walk-forward validation pipelines
- Feature engineering for price prediction
- Systems with train/live separation

## Key Risks

| Risk | Description | Rule ID |
|------|-------------|---------|
| **Data Leakage** | Future information in features | quant-001 |
| **Label Leakage** | Using shift(-N) for labels | quant-002 |
| **Train-Live Mismatch** | Feature divergence between environments | quant-003 |

## Additional Rules

This archetype adds 3 verification rules:

### quant-001: Leakage Shift Check
Scans for `rolling()`, `ewm()`, `pct_change()` without preceding `shift(1)`.

### quant-002: Label Shift Negative
Flags `shift(-N)` usage in label generation code.

### quant-003: Train-Live Sync
Checks that feature definitions exist in both training and deployment paths.

## Usage

1. Copy `memory.config.patch.json` to your project
2. Merge `paths_override` into `.memory/config.json`
3. Add `archetypes/quant/rules/verify-quant.rules.json` to `verify.rulesets`

Example config after merge:

```json
{
  "paths": {
    "CODE_ROOTS": ["src/"],
    "FEATURE_ROOTS": ["src/features/", "src/signals/"],
    "LABEL_ROOTS": ["src/labels/"],
    "DEPLOYMENT_ROOTS": ["deployment/"]
  },
  "verify": {
    "rulesets": [
      ".memory/rules/verify-core.rules.json",
      "archetypes/quant/rules/verify-quant.rules.json"
    ]
  }
}
```

## Recommended Tags

```
leakage, backtest, walk-forward, feature-engine, label, shift, rolling, train-live-sync
```
