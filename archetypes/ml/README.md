# ML Archetype

For machine learning pipelines, model training, and ML infrastructure projects.

## Target Projects

- ML training pipelines
- Feature engineering systems
- Model serving infrastructure
- Data processing workflows

## Key Risks

| Risk | Description | Rule ID |
|------|-------------|---------|
| **Data Leakage** | Test data in training | ml-001 |
| **Scaling Leakage** | Fitting scaler on full data | ml-002 |
| **Feature Drift** | Train/prod feature mismatch | ml-003 |

## Additional Rules

This archetype adds 3 verification rules:

### ml-001: Data Split Check
Looks for train/test split patterns to ensure proper separation.

### ml-002: Scaling Order Check
Flags potential scaling before split issues.

### ml-003: Feature Definition Sync
Checks that features are defined consistently across environments.

## Usage

1. Copy `memory.config.patch.json` to your project
2. Merge `paths_override` into `.memory/config.json`
3. Add `archetypes/ml/rules/verify-ml.rules.json` to `verify.rulesets`

## Recommended Tags

```
data-leakage, train-val-split, feature-scaling, model-drift, pipeline, preprocessing
```
