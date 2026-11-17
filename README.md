# BuildStock Rate Impact Calculator

A Streamlit web application for calculating the electricity bill impact of building upgrades using [ResStock](https://resstock.nlr.gov/) / [ComStock](https://comstock.nlr.gov/) simulation data. The app lets you:

- Select a BuildStock dataset and filter buildings by geography, vintage, and other characteristics
- Define a geographic scope (state, county, or PUMA)
- Configure baseline and upgraded electricity rate structures (flat rate or URDB tariffs via the [OpenEI Utility Rate Database](https://openei.org/wiki/Utility_Rate_Database))
- Run bill calculations across thousands of simulated buildings
- Explore results with interactive charts and maps

⬇️ **[Download the app](https://github.com/NatLabRockies/buildstock-rate-impact-calculator/releases)** — stand-alone zips for Windows, macOS and Linux; unzip and double-click, no installation needed

📖 **[Installation Instructions](https://natlabrockies.github.io/ComStock.github.io/docs/for_developers/bric_installation.html)** — step-by-step installation instructions for non-programmers

📖 **[User Guide](https://natlabrockies.github.io/ComStock.github.io/docs/resources/how_to_guides/bric_rate_impact_calculator.html#before-you-start)** — workflow walkthrough, how ResStock/ComStock
sampling weights work, and guidance on interpreting every results section.

---

## Installation

There are two ways to install the app:

- **Option 1 – Download the app** (recommended). No terminal, no other software and no administrator rights needed.
- **Option 2 – Run from source with pixi**. Use this if you want to change the code, or if Option 1 doesn't work on your computer.

### Option 1 – Download the app (recommended)

1. Go to the [Releases page](https://github.com/NatLabRockies/buildstock-rate-impact-calculator/releases) and find the newest release at the top.
2. Under **Assets**, download the zip file for your computer:

   | Computer | File |
   | --- | --- |
   | Windows | `BRIC-<version>-win-64.zip` |
   | Mac with Apple silicon (M1 or newer) | `BRIC-<version>-osx-arm64.zip` |
   | Mac with an Intel processor | `BRIC-<version>-osx-64.zip` |
   | Linux | `BRIC-<version>-linux-64.zip` |

3. **Windows only:** right-click the zip file, choose **Properties**, tick **Unblock** (if shown) and click **OK**.
4. Extract the zip anywhere, for example your Downloads folder.
5. Open the extracted folder and double-click the start file:
   - **Windows:** `Start BRIC.cmd`
   - **macOS:** `Start BRIC.command`
   - **Linux:** `start-bric.sh`

The app opens in your web browser. The first time you start a new version, it sets itself up, which takes a minute or so and needs about 1.5 GB of free disk space.

See the [Installation Instructions](https://natlabrockies.github.io/ComStock.github.io/docs/for_developers/bric_installation.html#option-1-download-the-app-recommended) for security warnings, updating, uninstalling and troubleshooting.

### Option 2 – Run from source with pixi

This option needs [pixi](https://pixi.sh), a fast, cross-platform package manager built on conda-forge.

#### 1. Install pixi

https://pixi.prefix.dev/latest/installation/

After installation, restart your terminal so `pixi` is available on your `PATH`.

#### 2. Clone the repository

```bash
git clone https://github.com/NatLabRockies/buildstock-rate-impact-calculator.git
cd buildstock-rate-impact-calculator
```

#### 3. Install the environment

```bash
pixi install
```

This resolves and installs all dependencies (Python, Polars, Streamlit, PySAM, etc.) into an isolated environment — no manual conda or pip steps required.

#### 4. Run the app

```bash
pixi run app
```

This starts the Streamlit development server. Open the URL printed in the terminal (usually `http://localhost:8501`) in your browser.

---

## OpenEI API Key

Some rate-lookup features require a free API key from OpenEI:

1. Register at <https://openei.org/services/api/signup/>
2. Paste your key into the **⚙ Setup → OpenEI API Key** field in the app sidebar

---

## Development

| Task | Command |
|------|---------|
| Run the app | `pixi run app` |
| Run tests | `pixi run test` |
| Lint | `pixi run lint` |
| Fetch utility data | `pixi run fetch-utilities` |
| Run as the stand-alone app does (free port, opens the browser) | `pixi run bric` |
| Build the stand-alone zips | `pixi run pack` (see [standalone/README.md](standalone/README.md)) |

---

## License

See [LICENSE](LICENSE.md) for details.

**NLR Software Record**: SWR-27-025 (Ringold, Eric; Klun, Lauren, "BuildStock Rate Impact Calculator", NLR Github 2026)