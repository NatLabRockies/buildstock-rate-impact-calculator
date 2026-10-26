# BuildStock Rate Impact Calculator

A Streamlit web application for calculating how building upgrades affect electricity bills using [ResStock](https://resstock.nlr.gov/) and [ComStock](https://comstock.nlr.gov/) simulation data.

With this app, you can:

- Choose a BuildStock dataset and filter buildings by geography, vintage, and other characteristics.
- Define a geographic scope (state, county, or PUMA).
- Set up baseline and upgraded electricity rate structures (flat rate or URDB tariffs from the [OpenEI Utility Rate Database](https://openei.org/wiki/Utility_Rate_Database)).
- Run bill calculations across thousands of simulated buildings.
- Explore results with interactive charts and maps.

There are two ways to install the app:

- **[Option 1 – Download the app](#option-1--download-the-app-recommended)** (recommended). Download one file and double-click it. No terminal, no other software and no administrator rights needed.
- **[Option 2 – Install with pixi](#option-2--install-with-pixi)**. Use this if you want to change the code, or if Option 1 doesn't work on your computer.

## Option 1 – Download the app (recommended)

### Download

1. Go to the latest release: <https://github.com/NatLabRockies/buildstock-rate-impact-calculator/releases/latest>
2. Under **Assets**, download the zip file for your computer:

   | Computer | File |
   | --- | --- |
   | Windows | `BRIC-<version>-win-64.zip` |
   | Mac with Apple silicon (M1 or newer) | `BRIC-<version>-osx-arm64.zip` |
   | Mac with an Intel processor | `BRIC-<version>-osx-64.zip` |
   | Linux | `BRIC-<version>-linux-64.zip` |

   On a Mac, choose **Apple menu → About This Mac**: "Chip: Apple M…" means Apple silicon; "Processor: Intel…" means Intel.

3. **Windows only:** before extracting, right-click the zip file, choose **Properties**, tick **Unblock** (if shown) and click **OK**. This prevents security warnings later.
4. Extract the zip: on Windows, right-click it and choose **Extract All...**; on a Mac, double-click it. Extract it anywhere (for example Downloads). You can delete the folder once the app has set itself up.

### Start the app

Open the extracted folder and double-click:

- **Windows:** `Start BRIC.cmd`
- **macOS:** `Start BRIC.command`
- **Linux:** `start-bric.sh` (or run `./start-bric.sh` in a terminal)

A window with some text opens, followed by the app in your web browser.

The **first time** you start a new version, the app sets itself up before opening. This takes a minute or two and needs about 2.5 GB of free disk space. Later starts take a few seconds.

To stop the app, click **⏻ Quit app** at the bottom of the app's sidebar, or close the text window. The app also stops on its own about 2 minutes after you close its browser tab.

#### Security warnings

The app isn't signed with a Microsoft or Apple certificate yet, so the first start may show a warning:

- **Windows – "Windows protected your PC":** click **More info**, then **Run anyway**. For an "Open File – Security Warning", click **Run**.
- **macOS – "cannot be opened because it is from an unidentified developer":** right-click (or Control-click) `Start BRIC.command`, choose **Open**, then **Open** again. You only need to do this once.

### Updating and uninstalling

- **Update:** download and extract the new version's zip and start it the same way. It sets itself up once and removes the previous version.
- **Uninstall:** delete the app's folder:
  - Windows: `%LOCALAPPDATA%\BRIC` (paste this into the File Explorer address bar)
  - macOS: `~/Library/Application Support/BRIC`
  - Linux: `~/.local/share/BRIC`

Updating or uninstalling never touches your downloaded data, projects or saved OpenEI API key. These live in the **Data Directory** shown in the app's **⚙ Setup** panel (by default `Documents/buildstock_results`).

Next, set up your [OpenEI API key](#openei-api-key-optional).

## Option 2 – Install with pixi

This installs pixi (a tool that manages Python and the app's packages), downloads the source code and runs the app from a terminal.

### What you'll need before you start

You do not need to know Python or VS Code, but you will use a **terminal** (a text window where you type commands).

You will need:

- A computer running Windows, macOS, or Linux.
- A web browser (Chrome, Edge, Firefox, Safari, etc.).
- An internet connection (to download the code and dependencies).
- Basic ability to copy and paste text.
- Administrative rights to install the pixi app and Python on your local machine.

### Step 1 – Install pixi

The project uses **pixi**, a tool that installs Python and all required packages for you, so you do not have to manually install Python or libraries.

1. Open a web browser and go to the pixi installation page: <https://pixi.prefix.dev/latest/installation/>
2. Follow the instructions on that page for your operating system:
   - On **Windows**, you will likely download an installer and run it. Or open PowerShell and paste the command:

     ```powershell
     powershell -ExecutionPolicy Bypass -c "irm -useb https://pixi.sh/install.ps1 | iex"
     ```

   - On **macOS** or **Linux**, you may run a command in the terminal.
3. When the pixi installation finishes, close any open terminal windows and then open a new terminal window.
   - This is important so that your system can find the pixi command.
4. To check that pixi is installed correctly, type this into the terminal and press Enter:

   ```bash
   pixi --version
   ```

   - If you see a version number, pixi is installed correctly.
   - If you see an error like `command not found`, go back to the pixi installation page and make sure you followed the steps for your operating system.

### Step 2 – Open a terminal

You will run all commands in a **terminal** (also called "Command Prompt" or "PowerShell" on Windows).

Here is how to open it:

- **Windows**: Press the Windows key, type `PowerShell` or `Command Prompt`, and click the result.
- **macOS**: Open **Finder**, go to **Applications → Utilities**, then open **Terminal**.
- **Linux**: Open **Terminal** from your applications menu.

Keep this terminal window open. You will paste commands into it in the next steps.

### Step 3 – Download the project

In this step you will download the project files from GitHub onto your computer.

If you already have **git** installed and know how to use it, you can use the `git clone` command shown below. If you do not have git, or you are not sure, use the ZIP download option.

#### Option A – Download as ZIP (recommended for beginners)

1. Open this link in your browser: <https://github.com/NatLabRockies/buildstock-rate-impact-calculator>
2. On the GitHub page, click the green **Code** button.
3. Click **Download ZIP**.
4. When the ZIP file finishes downloading, unzip it:
   - On **Windows**: Right-click the ZIP file and choose **Extract All...**
   - On **macOS**: Double-click the ZIP file.
   - On **Linux**: Use your file manager's extract option.
5. After unzipping, you should see a folder named something like `buildstock-rate-impact-calculator-main`.

#### Option B – Clone with git

In your terminal, copy and paste the following commands, then press Enter after each line:

```bash
git clone https://github.com/NatLabRockies/buildstock-rate-impact-calculator.git
cd buildstock-rate-impact-calculator
```

To paste into the terminal:

- **Windows**: Click in the terminal and press `Ctrl+V`.
- **macOS**: Click in the terminal and press `Command+V`.
- **Linux**: Click in the terminal and press `Ctrl+Shift+V`.

If you used Option A, do not run `git clone`. Continue to the next step.

### Step 4 – Move into the project folder

If you used the `git clone` option, you already moved into the folder and can skip to Step 5.

If you downloaded the ZIP file:

1. In the terminal, type `cd` (that is `cd` followed by a space).
2. Drag the **unzipped** project folder into the terminal window. This will paste the folder path automatically.
3. Press Enter.

It should look something like this:

```bash
cd "/Users/yourname/Downloads/buildstock-rate-impact-calculator-main"
```

To check that you are in the correct folder, type:

```bash
ls
```

On Windows Command Prompt, use:

```bat
dir
```

You should see files like `README.md` and `pixi.toml`.

### Step 5 – Install the project environment

Now that you are inside the project folder, install everything the app needs by running:

```bash
pixi install
```

This command downloads and installs Python and all required packages into an isolated environment.

The first run may take a few minutes. Wait until the command finishes and the terminal is ready again.

If you see an error saying pixi is not recognized or not found, return to Step 1 and make sure pixi is installed correctly and that you opened a new terminal window after installing it.

### Step 6 – Run the app

Once `pixi install` finishes successfully, start the app by running:

```bash
pixi run app
```

This starts the Streamlit development server. The terminal will print a local web address, usually:

```
http://localhost:8501
```

To open the app:

1. Copy the URL from the terminal.
2. Open your web browser.
3. Paste the URL into the address bar.
4. Press Enter.

The BuildStock Rate Impact Calculator should now open in your browser.

Each time you want to run the app again later:

1. Open a terminal.
2. Move into the project folder.
3. Run `pixi run app`.

Leave the terminal open while the app is running. To stop the app, click **⏻ Quit app** at the bottom of the app's sidebar, or press `Ctrl+C` in the terminal. The app also stops on its own about 2 minutes after you close its last browser tab.

## OpenEI API key (optional)

Some features, such as utility rate lookups, require a free API key from OpenEI.

1. Go to <https://openei.org/services/api/signup/>
2. Sign up and get an API key.
3. Open the app in your browser.
4. In the app sidebar, find the **OpenEI API Key** field.
5. Paste your API key into that field.

## Using VS Code (optional)

You do **not** need VS Code to run the app.

If you want to use VS Code anyway:

1. Download and install VS Code from <https://code.visualstudio.com/>.
2. Open VS Code.
3. Click **File → Open Folder...** and choose the project folder.
4. Click **Terminal → New Terminal**.
5. Run the same commands in the VS Code terminal:

   ```bash
   pixi install
   pixi run app
   ```

This is just another place to run the same commands.

## Development commands

If you are contributing to the project, run these commands from the project folder:

| Task               | Command                    |
| ------------------ | -------------------------- |
| Run the app        | `pixi run app`             |
| Run tests          | `pixi run test`            |
| Lint the code      | `pixi run lint`            |
| Fetch utility data | `pixi run fetch-utilities` |
| Build the stand-alone downloads | `pixi run pack` (see [standalone/README.md](../standalone/README.md)) |

## License

See the [LICENSE](../LICENSE.md) file in this repository for details.
