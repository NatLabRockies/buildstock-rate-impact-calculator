# BuildStock Rate Impact Calculator

A Streamlit web application for calculating the electricity bill impact of building upgrades using [ResStock](https://resstock.nlr.gov/) / [ComStock](https://comstock.nlr.gov/) simulation data. The app lets you:

- Select a BuildStock dataset and filter buildings by geography, vintage, and other characteristics
- Define a geographic scope (state, county, or PUMA)
- Configure baseline and upgraded electricity rate structures (flat rate or URDB tariffs via the [OpenEI Utility Rate Database](https://openei.org/wiki/Utility_Rate_Database))
- Run bill calculations across thousands of simulated buildings
- Explore results with interactive charts and maps

⬇️ **[Download the app](https://github.com/NatLabRockies/buildstock-rate-impact-calculator/releases/latest)** — stand-alone zips for Windows, macOS and Linux; unzip and double-click, no installation needed (see the installation instructions below)

📖 **[Installation Instructions](docs/bric-installation-instructions.md)** — installation instructions for non-programmers

📖 **[User Guide](docs/user_guide.md)** — workflow walkthrough, how ResStock/ComStock
sampling weights work, and guidance on interpreting every results section.

---

## Prerequisites

- [pixi](https://pixi.sh) — a fast, cross-platform package manager built on conda-forge

---

## Installation

### 1. Install pixi 

https://pixi.prefix.dev/latest/installation/

After installation, restart your terminal so `pixi` is available on your `PATH`.

### 2. Clone the repository

```bash
git clone https://github.com/NatLabRockies/buildstock-rate-impact-calculator.git
cd buildstock-rate-impact-calculator
```

### 3. Install the environment

```bash
pixi install
```

This resolves and installs all dependencies (Python, Polars, Streamlit, PySAM, etc.) into an isolated environment — no manual conda or pip steps required.

---

## Running the App

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
