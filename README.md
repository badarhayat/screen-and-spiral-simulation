# Spiral concentrator simulator

An interactive Python program that simulates a spiral concentrator for any mineral suite. It uses a sequential two-cut, size-density partition model of the kind described for gravity separators in R.P. King, *Modeling and Simulation of Mineral Processing Systems*, Chapter 7.

The same equations are used for every ore. Minerals are whatever you name, each with a density you supply. Heavy-mineral sands, iron ore, chromite, and coal are handled by the same model. Coal is the case where the concentrate is the light outer product rather than the dense inner product.

The program uses only the Python standard library (3.9 or newer).

## Run it in a browser

From this directory:

```bash
python3 -m spiral_sim --web
```

Then open [http://127.0.0.1:8080](http://127.0.0.1:8080). The page asks for the feed one step at a time, runs the same model as the command-line program, and lets you change the splitters or the cut densities and run again. On the results page, **← Back** returns to the setup and keeps the feed, settings, and results. Open Results again, or use the browser’s Back and Forward buttons, and the same results come back when the inputs are unchanged. **New simulation** asks before it clears them. Leave the terminal open while you use it. Ctrl+C stops the server.

Every change is written to a workspace file after a short pause. Reopening the page, including after the Windows program is closed and started again, restores that file. **Discard changes** returns to the last simulation. **Reset simulation** asks, then deletes the file. **Decimal precision** shows 4 or 5 decimal places. The partition math still uses the full values. Export backup and Import backup copy that same file.

On Windows the file is `%LOCALAPPDATA%\SpiralConcentrator\workspace.json`. On Linux and macOS it is `~/.local/share/SpiralConcentrator/workspace.json`. Set `SPIRAL_WORKSPACE_DIR` to keep it somewhere else. A file that cannot be read is left as it is.

```bash
python3 -m spiral_sim --web --port 8080
```

## Run it in the terminal

```bash
python3 -m spiral_sim
python3 -m spiral_sim --example          # heavy-mineral sands, then exit
python3 -m spiral_sim --demo-fit         # fit a synthetic survey, then exit
python3 -m spiral_sim --session my.json  # load a saved session, then exit
```

The built-in heavy-mineral example is also saved in `examples/heavy_mineral_sands.json`.

## How the conversation works

Questions are asked one at a time.

1. Feed size classes: lower size, upper size, and mass percent, one class at a time.
2. Minerals: name and density (g/cm3, the same number as specific gravity).
3. Mineral grades, either overall or by size class. With only overall grades, every size class is given that composition.
4. Optional feed rate (t/h) and percent solids.
5. Whether the concentrate is the dense product or the light product, and whether you want two products or three.
6. Whether you have plant data.
   - If you do, the program asks for yields, grades, and/or recoveries, one number at a time. Press Enter to skip anything you do not have. It then fits the cut densities, sharpness, and bypass.
   - If you do not, it places the cuts between your lightest and heaviest minerals and uses typical spiral sharpness and bypass. The report says that those results are approximate.
7. Splitter openings, or a target mass yield.

After the first result you can change splitter openings, match a mass yield, edit any partition parameter, calibrate, or save, without entering the feed again.

## What the report contains

For concentrate, middlings, and tails (middlings are omitted when you ask for two products):

- Mass yield, and solids t/h when you supplied a feed rate
- Grade of each mineral
- Recovery of each mineral
- Size distribution of each product
- Where each size class reports
- A mineral balance per 100 mass units of feed, with closure

It also prints the partition parameters that were used, including the size-by-size cut density, Ecart probable, and bypass. If a plant survey is stored, the report compares it with the prediction.

Every report ends with a note that results improve significantly after calibration with real spiral data.

## The model

Recovery of a particle depends on its size and its density. At each size the density curve is an error function (a logistic sigmoid is available in the parameter editor). The corrected curve is 50% at the cut density and 75% one Ecart probable above it.

Cut density and imperfection both change with size. With the default size exponents, finer particles have a higher cut density and a blunter curve, which is the usual spiral trend: drag keeps fines from moving inward unless they are denser. A size-dependent bypass short-circuits some solids to the outer product (entrainment). A small bypass to the inner product is included as well.

Three products are two of these cuts in series:

1. The outer splitter separates tails from the combined inner product.
2. The inner splitter separates that inner product into concentrate and middlings.

Two products use the outer cut only. Bypass is applied once, to the feed, in the short-circuit form used for gravity partition curves: a fraction misses the separation entirely, and the rest follows the corrected curve.

Percent solids is not a primary partition variable. Around a typical 30% solids there is a mild correction: a thicker pulp widens the Ecart probable and increases fine-particle entrainment. It is only a correction. Calibration still dominates.

Splitter openings use a 0 to 100 scale. Opening 50 applies the stored reference cut densities. A higher opening lowers the cut density and sends more solids to the inner, denser stream. Values outside 0 to 100 are allowed when a requested mass pull needs them, and the report says so.

## Defaults and calibration

With no plant data the reference cuts are placed 35% and 70% of the way from the lightest mineral to the heaviest. That is a rougher/cleaner starting point, not a measurement. Imperfection starts at 0.10 at 0.15 mm. A heavy-mineral textbook preset (cuts of 3.20 and 3.95 g/cm3 at 0.15 mm) is available from the parameter editor when that is actually the duty.

With plant data, a least-squares search adjusts:

- outer cut density
- inner cut density (three products)
- imperfection
- coarse bypass and the extra fine-particle bypass

Size exponents stay at their starting values unless you also enter the mass split of each size class. Bulk assays do not identify those exponents. The fit report shows the average error in percentage points and a side-by-side of observed and fitted numbers. You can keep or discard the fit.

Fitted parameters are the opening-50 cuts. If the survey was taken at another opening, say so, and the fit accounts for it.

## Save and load

From the menu, save writes a JSON file with the feed, parameters, splitter openings, and any plant survey. Fractions in the file run from 0 to 1. The conversation speaks in percent. Load that file later, or pass it to `--session`.

## Windows app

People who do not have Python can run `SpiralConcentrator.exe`. Double-click it. A console window stays open, the simulator starts, and the browser opens at [http://127.0.0.1:8080](http://127.0.0.1:8080). Leave that console window open while you use the program. Closing it stops the simulator.

From a Command Prompt the same flags as the Python program work, including the browser mode:

```bat
SpiralConcentrator.exe --web
SpiralConcentrator.exe --web --port 8081
SpiralConcentrator.exe --example
SpiralConcentrator.exe --cli
```

A double-click is the same as `--web`. `--cli` starts the step-by-step terminal conversation instead. `--no-browser` starts the page without opening a browser on its own.

The executable has to be built on Windows. PyInstaller cannot produce an `.exe` from Linux or macOS.

```bat
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

That writes `dist\SpiralConcentrator.exe`. The PyInstaller configuration is `packaging\SpiralConcentrator.spec`. It packs `spiral_sim` and the browser page `spiral_sim\static\index.html` into one file, so rebuild with the same command after a page change. The application name is Spiral Concentrator and the icon is `packaging\spiral.ico`. The build is for 64-bit Windows.

To make an installer later, install [Inno Setup 6](https://jrsoftware.org/isinfo.php) and compile `packaging\SpiralConcentrator.iss`. The installer is `dist\SpiralConcentrator-Setup.exe`. It does not need administrator rights. It adds a Start menu entry and, if chosen, a desktop icon.

## Tests

```bash
python3 -m unittest tests.test_spiral tests.test_web -v
```

The tests check the 50% and 75% points of both partition curves, mass and mineral closure, heavy-versus-light recovery, coal (light concentrate), a single mineral, two products, splitter direction, target mass pulls, calibration against a synthetic survey, save/load, and the interactive dialogue.

## Limits

This is a steady-state solids partition model, not a full fluid-dynamic model of the trough. Water split is not calculated. Percent solids only applies the mild correction above. Results from the density-based starting point are approximate until you calibrate them to a survey from the spiral you are actually running. A second, independent survey is the right check before using a fitted model for design.
