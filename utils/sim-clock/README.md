# sim-clock

Configurable single tick master of an OpenADSim run. It is **opt-in**: with `SIM_TIME_MODE` empty (the default) the service exits right away and the simulators keep their own timing.

| `SIM_TIME_MODE` | Behaviour | Use |
| --- | --- | --- |
| `realtime` | Steps on a wall-clock schedule at the real-time factor `SIM_RT_FACTOR`. A late step is counted, never caught up with a burst. Never waits for the system under test. | Is the system under test fast enough for real-world deployment? |
| `gated` | Steps, then waits until every gate member has finished the step. | Slow components of the system under test get the time they need; lossless recording |
| `fast` | Steps as fast as possible. | Warm-up, data collection where everything keeps up |

## Warm-up

A warm-up of `WARMUP_S` sim seconds runs the simulation as fast as possible, without pacing or gate, before the main phase. Only the main phase counts: the late steps, the verdict, the gate statistics and `SIM_CLOCK_STOP_AFTER_SIM_S` refer to it.

- **Default** (`WARMUP_S` empty): 60 sim seconds for `realtime` with `SIM_RT_FACTOR=auto`, which measures the achievable factor during the warm-up (at least 10 s). No warm-up in every other mode, so `gated` is gated from the first step.
- **Set it explicitly** to keep the start of a run out of the main phase, in any mode:
  - `realtime` at a fixed X: the first 30–60 s of a heavy map are often much slower (traffic insertion, asset streaming) and would otherwise count as late steps.
  - `gated` or `fast`: to let the traffic managing entity fill the map with traffic first.

## Backends

| Profile | Service | What sim-clock advances |
| --- | --- | --- |
| `carla` | `sim-clock-carla` | the CARLA world (`world.tick()`); needs `CARLA_BRIDGE_PASSIVE=true` |
| `sumo` | `sim-clock-sumo` | SUMO of `sumo_interface` as its TraCI client (TraCI server enabled in `sumo-simulation/sumo_interface/params.yml`) |

## Real-time factor (`SIM_TIME_MODE=realtime`)

`SIM_RT_FACTOR` is `auto` (default) or one of `0.25`, `0.5`, `0.75`, `1.0`. `auto` measures the free-running factor in the second half of the warm-up and takes the largest choice whose 5th percentile, times 0.98, it reaches (so `WARMUP_S` must be at least 10).

At a factor X < 1 the system under test gets 1/X times more wall time per sim second than in reality:

- if it **fails** at X < 1, it fails in real-world deployment as well (conservative, valid on any hardware);
- if it **passes**, that holds for deployment hardware on which it runs at least 1/X times faster. Report X, and the processing time measured in `gated` mode.

A `realtime` run is valid if, after the warm-up, at least 99 % of the 1 s windows reach 0.98 · X. `clock_summary.json` contains the verdict (`validity`).

## Gate (`SIM_TIME_MODE=gated`)

In `gated` mode the simulation waits for the system under test after every step. A slow component then gets all the time it needs, and in sim time it behaves as if it were infinitely fast. The components the simulation waits for are the **gate members**.

### How a step works

1. sim-clock advances the simulation by one step (0.05 s). The simulator publishes the new sensor data and `/clock`, stamped with the sim time t of the step.
2. sim-clock publishes `/simulation/tick` with stamp t.
3. sim-clock waits until every gate member has finished t:
   - a **gate topic** has finished t when a message with `header.stamp` ≥ t arrives on it;
   - a **gate participant** has finished t when it publishes `/simulation/step_done` with a stamp ≥ t.
4. Then the next step follows. If a member has not finished after `GATE_TIMEOUT`, see [Timeouts](#timeouts).

Before the first gated step, sim-clock waits until every member has reported once (the **join**, at most `GATE_JOIN_TIMEOUT`, default 300 s), so that slow-starting components are not counted as timeouts. Members can only report after an input, so during the join sim-clock steps the simulation once per wall second, without gate. A member that takes 20 s to start therefore misses about 20 steps rather than a whole warm-up.

### Gate topics or gate participants?

**Start with `GATE_TOPICS`. It needs no change to the system under test.** Name the output topic that the component publishes when it has processed a step:

```bash
SIM_TIME_MODE=gated
GATE_TOPICS=/simulation/object_list
```

A topic works as a gate member if all of this holds:

- its message type has a `header`;
- it publishes once per step, or at most every P sim seconds (see [Members that do not publish every step](#members-that-do-not-publish-every-step));
- `header.stamp` is the stamp of the input it was computed from. A node with `use_sim_time` that stamps with `now()` usually works as well: while sim-clock waits, the sim time does not advance, so `now()` is the step's stamp once the node has received the step's `/clock`. Stamping with the input's stamp is the reliable choice.

Gating on the **last output of a pipeline** covers every node before it, so one topic is often enough. Several topics are possible as well: the step waits for all of them.

**Use a gate participant only if a topic does not work.** A participant has to publish `/simulation/step_done` itself, so its source code has to be changed, by a few lines (see [`examples/ack_example.py`](examples/ack_example.py)). In return it covers these cases:

| Case | Gate topic | Gate participant |
| --- | --- | --- |
| The output has no header (e.g. `geometry_msgs/Twist`, `ackermann_msgs/AckermannDrive`, `std_msgs` types) | cannot be assigned to a step | acknowledges explicitly |
| No output on some steps (publishes only on change, or has nothing to report) | the step waits for the full `GATE_TIMEOUT` | acknowledges "nothing to do" |
| "Finished" means more than "published" (written to disk, flushed, an external process done) | only sees the message | acknowledges when it is really done |
| Large outputs (images, point clouds) | sim-clock has to receive and deserialize every message | the acknowledgement is a few bytes |
| The component is not a ROS publisher | nothing to subscribe to | one publish call |

Topics and participants can be mixed. The step waits for all of them.

### Gate topics: `GATE_TOPICS`

A comma-separated list of topics. sim-clock subscribes to each topic as soon as it appears in the ROS graph, with any message type that has a header.

- **Patterns:** glob patterns (`*`, `?`, `[]`) and regular expressions (starting with `^`, without commas) add every matching topic as soon as it is published, e.g. `/carla/ego_vehicle/*/image/compressed` for all compressed cameras. A pattern only counts as joined once at least one topic matches it.
- **Message types:** besides the ROS and CARLA types, the image contains the OpenADStack types `perception_msgs`, `trajectory_planning_msgs` and `route_planning_msgs`, e.g. for `/simulation/object_list`.
- **QoS:** sim-clock subscribes best effort, so it matches reliable and best-effort publishers.

### Gate participants: `GATE_PARTICIPANTS`

A comma-separated list of participant ids. After processing a step, each participant publishes a `std_msgs/Header` on `/simulation/step_done` with:

- `stamp`: the stamp of the input it has finished (or the stamp of the latest `/simulation/tick`);
- `frame_id`: its id, exactly as in `GATE_PARTICIPANTS`.

The stamp is what matters: an acknowledgement for a later stamp also finishes earlier steps. [`examples/ack_example.py`](examples/ack_example.py) is a complete participant. It subscribes to a camera, simulates `--compute-ms` of processing and acknowledges the image's stamp:

```bash
# .env
SIM_TIME_MODE=gated
GATE_PARTICIPANTS=example_participant

# next to the running stack, in the sim-clock image:
docker compose run --rm --no-deps sim-clock-carla bash -ic \
  "python3 examples/ack_example.py --topic /carla/ego_vehicle/rgb_view/image --compute-ms 200"
```

### Members that do not publish every step

Without a period, a member has to finish **every** step. A LiDAR with `sensor_tick: 0.1` at 0.05 s steps publishes only on every other step, so each of the other steps would wait for the full `GATE_TIMEOUT`.

For such members, append `@P`, where P is the longest expected interval between two outputs in sim seconds. This works in both lists:

```bash
GATE_TOPICS=/simulation/object_list,/carla/ego_vehicle/*/point_cloud@0.1
GATE_PARTICIPANTS=my_planner@0.1
```

With `@P`, a step only waits for the member when its newest stamp is P or more behind the step. Steps on which it does not publish pass without waiting, whichever steps it publishes on, and it can never fall a whole period behind. Take P from the sensor tick or the timer period. A larger P lets the member lag further.

### Timeouts

- `GATE_TIMEOUT` (default 10 s): how long a step waits for its members.
- `GATE_ON_TIMEOUT`: `continue` (default) steps on and counts the timeout; `abort` ends the run with exit code 3. Use `abort` when a run has to be reproducible, since a timeout depends on wall time.

Timeouts and gate waits per member (p50/p95/max) are in `/simulation/clock_status` and `clock_summary.json`. The gate wait is the processing time to report together with a real-time factor X.

### Topics of sim-clock

| Topic | Type | Content |
| --- | --- | --- |
| `/simulation/tick` | `std_msgs/Header` | published by sim-clock after every step: `stamp` = sim time of the step, `frame_id` = step index |
| `/simulation/step_done` | `std_msgs/Header` | published by a gate participant: `stamp` = the stamp of the input it has finished, `frame_id` = its id |
| `/simulation/clock_status` | `diagnostic_msgs/DiagnosticArray` | 1 Hz, stamped with the sim time of the latest step: mode, phase (`warmup`, `join`, `main`), target and achieved factor, late steps, gate waits per member |

## Duration

`SIM_CLOCK_STOP_AFTER_SIM_S` ends the main phase after this many sim seconds (0, the default: run until stopped). sim-clock then exits; CARLA continues asynchronously, SUMO waits for its next TraCI client.

## Output

`clock_summary.json` in `SIM_CLOCK_DIRECTORY` (default `utils/sim-clock/output`): mode, factor (requested and chosen), steps, late steps, achieved factor and validity of the main phase, gate statistics per member (p50/p95/max, timeouts), CARLA frames per step (lockstep check, 1.0) and CARLA world resets.