# Production Line

A work order goes down a production line. The Line Supervisor agent calls the first stage, and
each stage is a **tool** that does its work and then calls the next.

```
Work Order (start) ──▶ Line Supervisor (agent) ──────────────────────────────▶ Shipping (end)
                              │
                              ▼ first_stage
                             Cut ──▶ Weld ──▶ Paint ──▶ Inspection
                         (each stage calls the next through its next_stage port)
```

It shows how to write a **tool** component, call a tool from an agent and a tool from another
tool with `perceivo.flow.invoke`, and declare **tool ports** in YAML.

| File | What it is |
| --- | --- |
| `production-line.cborchestration.json` | The import file for the platform. |
| `run.ipynb` | Runs the orchestration on your machine. |
| `orchestration.yaml`, `perceivo.yaml`, `input.json` | The graph, the local config and the sample input, used by the local run. |
| `.gitignore` | Keeps the local run's `working_files/` and `.perceivo/` folders, and any `.env`, out of git. |
| `work-order/`, `line-supervisor/`, `cut/`, `weld/`, `paint/`, `inspection/`, `shipping/` | One folder per component: its code, its YAML, its import file and a README. |

## What you need to configure

- **Model:** none. No component calls a model.
- **Secrets:** none. No component declares a required secret.
- **Parameters:** none. No component declares input parameters.
- **Input:** two fields, `product` (what to make) and `seconds_per_stage` (how long each of the
  four stages works, 1 to 30). `input.json` uses `{"product": "bicycle frame", "seconds_per_stage": 5}`,
  so a run takes about 20 seconds.

## Run it on the platform

1. **Import.** Open **Orchestrations**, choose **Import**, and pick
   `production-line.cborchestration.json`. The import lays the four stages out in a column under
   the Line Supervisor, each with its own shape and colour.
2. **Secrets.** Nothing to attach: this example uses no model and no secrets, so it imports ready
   to run.
3. **Parameters.** Nothing to set: no component has parameters.
4. **Publish.** Optional layout first: to see the hand-offs, select **Weld**, and in the Inspector
   under **Ports** set its `input` port's position to **Left**. Do the same for **Paint** and
   **Inspection**, then move each stage down and to the right of the one before it (double-click a
   node to enter move mode, drag it, then press **Escape**). Then **Save draft** and **Publish**.
5. **Run.** Open **Runner** and run it with the fields from `input.json`. Each stage lights up in
   turn, and the run reaches **Completed** in under a minute. The run's **Logs** show each stage
   finish.

## Run it locally with the SDK

```bash
pip install perceivo==1.0.0
pip install notebook
jupyter notebook run.ipynb
```

Open `run.ipynb` from inside this folder and run all cells. It runs the orchestration on
`input.json` and prints what Shipping returned, plus each node's step in the trace.

Two things to expect:

- The trace lists the steps in the order they **finish**, so the stages appear in reverse:
  Inspection, Paint, Weld, Cut. Each stage waits for the one after it before it returns.
- The runner warns that it is ignoring the `appearance` and `port_sides` config keys. Those keys
  only set the node's look on the canvas, so the warning is harmless.

## Shapes and colours

Each node's look is set in `orchestration.yaml`, under the node's `config.appearance`:

```yaml
- id: node_cut
  name: Cut
  config:
    port_sides:
      input: top
    appearance:
      icon: zap
      shape: diamond
      size: sm
      color: red
```

`port_sides` puts a port on one side of the node: `left`, `top`, `right` or `bottom`. A tool port
always sits on the bottom. An import lays the ports out afresh, which is why step 4 above sets them
by hand.

Shapes: `rounded`, `circle`, `diamond`, `hexagon`, `cylinder`, `document`, `cloud`, `queue`.
Sizes: `sm`, `md`, `lg`. Colours: `slate`, `blue`, `indigo`, `violet`, `pink`, `red`, `orange`,
`amber`, `green`, `teal`.
