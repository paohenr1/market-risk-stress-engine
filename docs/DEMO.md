# Synthetic dashboard

The [interactive demonstration](demo/index.html) uses entirely invented market and macroeconomic observations. It illustrates the research pipeline's model comparison, scenario construction, fixed-time portfolio repricing and attribution. It does not report empirical investment performance or establish forecast accuracy.

When served through GitHub Pages, open `demo/` to use the dashboard. GitHub's repository file viewer displays HTML source instead of running the page. The same `index.html` can be downloaded and opened locally in a modern browser; it has no external assets, analytics or API calls.

## Rebuild

From a source checkout with Python 3.11 or newer, run:

```powershell
python tools/build_demo.py --output outputs/demos/rebuilt
```

Open `outputs/demos/rebuilt/index.html`. Use a new output directory for each build. No credentials, provider access or optional rendering packages are required. The generator uses the deterministic observations in `tests/test_models.py` and the integrated fixture in `tests/test_execution.py`, then fits, evaluates and reprices them with the normal pipeline. It accepts no path to external data. Run IDs, timestamps and runtime metadata vary between builds; the observations and calculations are deterministic under the same source and runtime.

The replay cases use fictional observations, even where their dates overlap actual historical events. Conditional cases use invented recession and stagflation assumptions. Their results can differ from the real-data study because the selected models can differ. The generator does not replace selected models or edit calculated results to manufacture a scenario difference.

The generated HTML embeds only the viewer's result fields. `dashboard_manifest.json` records content hashes and the captured source identity; hashes establish integrity, not financial validity. The saved intermediate run exists only in a temporary directory during generation. The published demo contains no subscribed observations, credentials or real-data results.
