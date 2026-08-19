# OpenADSim

<p align="center">
  <a href="https://openads-project.github.io"><img src="https://img.shields.io/badge/OpenADS-45ccc6"/></a>
  <a href="https://www.ros.org"><img src="https://img.shields.io/badge/ROS 2-jazzy-22314e"/></a>
  <a href="https://github.com/openads-project/openadsim/releases/latest"><img src="https://img.shields.io/github/v/release/openads-project/openadsim"/></a>
  <a href="https://github.com/openads-project/openadsim/blob/main/LICENSE"><img src="https://img.shields.io/github/license/openads-project/openadsim"/></a>
</p>

**Simulation environment for testing [OpenADStack](https://github.com/openads-project/openadstack) with CARLA or SUMO**

OpenADSim is the official environment for closed-loop simulation of [*OpenADStack*](https://github.com/openads-project/openadstack). It supports examplaric demonstrations, prototyping, and scenario-based testing with CARLA and SUMO.

<p align="center">
  <strong>🚀 <a href="#-quick-start">Quick Start</a></strong> • <strong>🧪 <a href="#-examples">Examples</a></strong> • <strong>📐 <a href="#-architecture">Architecture</a></strong> • <strong>📝 <a href="#-documentation">Documentation</a></strong> • <strong>🙏 <a href="#-acknowledgements">Acknowledgements</a></strong>
</p>

> [!NOTE]
> This repository is part of [***OpenADS***](https://openads-project.github.io), the *Open Automated Driving Systems* project. *OpenADS* and its modules have been initiated and are currently being maintained by the [**Institute for Automotive Engineering (ika) at RWTH Aachen University**](https://www.ika.rwth-aachen.de/de/).

<div align="center">
  <a href="https://rwth-aachen.sciebo.de/s/Mb2n5acz38QaKck/download"><img src="https://github.com/user-attachments/assets/0517cb48-d01d-4aa2-a86c-625beac45cc4" width="720" style="max-width: 100%;" alt="OpenADSim Teaser"/></a>
  <br>
  <em><a href="https://rwth-aachen.sciebo.de/s/Mb2n5acz38QaKck"> Watch the full teaser video</a>.</em>
</div>

## 🚀 Quick Start

> [!IMPORTANT]
> Check the general [OpenADS system requirements](https://openads-project.github.io/start/start.html#requirements) and install [Git LFS](https://git-lfs.com/). Graphical applications require access to a local X11 server.

```bash
git lfs install
git clone --recursive https://github.com/openads-project/openadsim.git
cd openadsim
```

Start OpenADSim with the default configuration:

```bash
xhost +local:
docker compose up -d
```

> [!NOTE]
> The initial image pull may take several minutes, depending on your system and internet connection.

Once the RViz and Manual Control windows open, you can plan a route with the `Plan Route` tool in RViz. Alternatively, drive manually with `W/A/S/D` and press `B` in Manual Control to hand control back to OpenADStack.

> [!IMPORTANT]
> **Continue with the [Getting Started guide](./docs/getting-started.md).** It covers the first simulation steps including usage and configuration.

## 📝 Documentation

The documentation covers:

- [Getting Started](./docs/getting-started.md)
- [Architecture](./docs/architecture.md)
- [Configuration](./docs/configuration.md)
- Examples
  - [Scenario Execution](./docs/example-scenario-execution.md)
  - [Cooperative Perception](./docs/example-cooperative-perception.md)
  - [ML Planning](./docs/example-ml-planning.md)
- [Custom Integration](./docs/custom-integration.md)

## 🧪 Examples

The links below provide detailed setup examples demonstrating the capabilities of OpenADSim and [OpenADStack](https://openads-project.github.io/openadstack/openadstack.html). Feel free to use these templates to customize your own simulation setups.

| Example | Compose Profiles | Description |
| ------ | ------ | ------ |
| ***prototyping*** | `carla`, `traffic`, `manual-testing`, `no-perception`, `planning` (or `sumo`, `no-perception`, `planning`) | **Default** setup for experimenting with and manually testing OpenADStack. Traffic and testing profiles apply only to CARLA. |
| [***scenario-execution***](./docs/example-scenario-execution.md) | `carla`, `no-traffic`, `automated-testing`, `no-perception`, `planning` | Sequential simulation of **multiple scenarios** in OpenSCENARIO format with automated evaluation. |
| [***cooperative-perception***](./docs/example-cooperative-perception.md) | `carla`, `traffic`, `no-testing`, `perception`, `planning` | Intersection scenario with multiple sensor-equipped participants sharing perception via **V2X**. |
| [***ml-planning***](./docs/example-ml-planning.md) | `carla`, `no-traffic`, `manual-testing`, `no-perception`, `ml-planning` | Partial offloading of planning tasks to a **machine learning-based planning** module with adaptive orchestration. |
| ... | ... | ... |

## 📐 Architecture

The detailed architecture and service structure are described in [Architecture and Services](./docs/architecture.md).

OpenADSim combines the following elements:

- **Simulation**: simulator backends, scenario execution, environment control, and simulator-specific ROS integration
- **OpenADStack**: localization, perception, understanding, planning, and control services
- **Supporting Services**: communication infrastructure, visualization, and data recording
- **Configuration and Data**: maps, scenarios, vehicle and sensor configurations
- **Deployment and Orchestration**: Docker Compose configurable using environment variables and profiles, with a supporting Configuration GUI and predefined presets

Together, these elements form a modular simulation environment in which individual components can be configured and exchanged independently.

## 🙏 Acknowledgements

### Citation

We hope that OpenADSim can help your research. If this is the case, please cite it using the metadata specified in [CITATION.cff](https://github.com/openads-project/openadsim/blob/main/CITATION.cff), or click on Cite this repository in GitHub's About section on the top right.

### Related Publications

<details>

<summary><strong>CARLOS: An Open, Modular, and Scalable Simulation Framework for the Development and Testing of Software for C-ITS, 2024</strong></summary>

> *([IEEEXplore](https://ieeexplore.ieee.org/document/10588502), [arXiv](http://arxiv.org/abs/2404.01836), [ResearchGate](https://www.researchgate.net/publication/379484629_CARLOS_An_Open_Modular_and_Scalable_Simulation_Framework_for_the_Development_and_Testing_of_Software_for_C-ITS))*  
>
> [Christian Geller](https://www.ika.rwth-aachen.de/de/institut/team/fahrzeugintelligenz-automatisiertes-fahren/geller.html), [Benedikt Haas](https://github.com/BenediktHaas96), [Amarin Kloeker](https://www.ika.rwth-aachen.de/en/institute/team/vehicle-intelligence-automated-driving/kloeker-amarin.html), [Jona Hermens](https://ieeexplore.ieee.org/author/649463586767210), [Bastian Lampe](https://www.ika.rwth-aachen.de/en/institute/team/vehicle-intelligence-automated-driving/lampe.html), [Till Beemelmanns](https://www.ika.rwth-aachen.de/en/institute/team/vehicle-intelligence-automated-driving/beemelmanns.html), [Lutz Eckstein](https://www.ika.rwth-aachen.de/en/institute/team/univ-prof-dr-ing-lutz-eckstein.html)
> [Institute for Automotive Engineering (ika), RWTH Aachen University](https://www.ika.rwth-aachen.de/en/)
>
> <sup>*Abstract* – Future mobility systems and their components are increasingly defined by their software. The complexity of these cooperative intelligent transport systems (C-ITS)  and the ever-changing requirements posed at the software require continual software updates. The dynamic nature of the system and the practically innumerable scenarios in which different software components work together necessitate efficient and automated development and testing procedures that use simulations as one core methodology. The availability of such simulation architectures is a common interest among many stakeholders, especially in the field of automated driving. That is why we propose CARLOS - an open, modular, and scalable simulation framework for the development and testing of software in C-ITS that leverages the rich CARLA and ROS ecosystems. We provide core building blocks for this framework and explain how it can be used and extended by the community. Its architecture builds upon modern microservice and DevOps principles such as containerization and continuous integration. In our paper, we motivate the architecture by describing important design principles and showcasing three major use cases - software prototyping, data-driven development, and automated testing. We make CARLOS and example implementations of the three use cases publicly available at [https://github.com/ika-rwth-aachen/carlos](https://github.com/ika-rwth-aachen/carlos).</sup>

</details>

### Licensing

The source code in this repository is licensed under Apache-2.0, see [LICENSE](https://github.com/openads-project/openadsim/blob/main/LICENSE). Container images provided by this repository may contain third-party software shipped with their own license terms.

### Funding

Development and maintenance of this repository are supported by the following projects. We acknowledge the funding of the respective institutions.

| Project | Funding Institution | Grant Number |
| --- | --- | --- |
| [AIGGREGATE](https://aiggregate.eu/) | 🇪🇺 European Union | 101202457 |
| [AIthena](https://aithena.eu/) | 🇪🇺 European Union | 101076754 |
| [autotech.agil](https://www.autotechagil.de/en/) | 🇩🇪 Federal Ministry for Research, Technology and Space (BMFTR) | 01IS22088A |
| [iEXODDUS](https://iexoddus-project.eu/) | 🇪🇺 European Union | 101146091 |

<p>
  <img src="https://www.drought.uni-freiburg.de/stressres/images/bmftr-logo/image" height=70>
  <img src="https://ec.europa.eu/regional_policy/images/information-sources/logo-download-center/eu_funded_en.jpg" height=70>
</p>

<sub><sup>Funded by the European Union. Views and opinions expressed are however those of the author(s) only and do not necessarily reflect those of the European Union or the European Climate, Infrastructure and Environment Executive Agency (CINEA). Neither the European Union nor CINEA can be held responsible for them.</sup></sub>
