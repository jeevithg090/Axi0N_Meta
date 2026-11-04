# Crowd watch: market research for festivals, stadiums, stations, religious gatherings and malls

Researched 4 Nov 2026 for the Meta Global AI Developer Hackathon. Each claim has a link. "Unverified" means the source is secondary, the source disagrees with others, or the session did not re-check it. Death tolls come from the cited sources. Where sources disagree, both figures are given.

A note on words: many reports call these events "stampedes". Crowd scientists say people in a crush die because they cannot move, not because they panic. A 2012 analysis of the Love Parade found no sign of mass panic ([phys.org](https://phys.org/news/2012-06-panic-parade-disaster-germany.html)). This report says "crush" unless it is quoting a report.

**Our numbers, for context** (`reports/eval/2026-11-03-crowd-live.md`): the test footage was a real Jülich bottleneck experiment, scored against published trajectories.

- SAM 3.1 counted 95–99% of the true heads in each zone.
- Gate density was within about 10% of the truth, up to 9 people per m².
- A reading became an alert in about 40 s.
- Glimmer had no GPU during that run, so both alerts went out unchecked, and said so.
- Corridor density read 22% low because the zone reached past the floor points.

---

## 1. Lessons from disasters

### What went wrong

| Event | What went wrong | How much warning there was |
|---|---|---|
| **Hillsborough, Sheffield, 1989** (96 or 97 died; sources differ) | Pens 3 and 4 were full by 2:50 pm. Taylor said the tunnel to them "should have been closed off whether gate C was to be opened or not". Gate C was opened on police orders and fans walked straight into the full central pens ([Taylor Report](https://en.wikipedia.org/wiki/Taylor_Report); [IOPC timeline](https://www.policeconduct.gov.uk/hillsborough-report/chapter-4/timeline-15-april-1989)). The control box had CCTV of the pens, but nobody had the specific job of watching them fill. Capacity was judged by eye, with "no mechanical or electronic means" of calculating it (secondary summaries of the inquiry evidence, unverified wording). The 2012 Independent Panel said crowd safety was "compromised at every level" ([Hansard extracts](https://parallelparliament.co.uk/debate/2012-10-22/commons/commons-chamber/hillsborough)). | About 10 minutes or more between "pens full" and the crush, and the crush was visible on CCTV. |
| **Love Parade, Duisburg, 2010** | Analysts called it a systemic failure: several factors interacting, ending in "crowd turbulence" (Helbing and Mukerji, [arXiv 1206.5856](https://arxiv.org/abs/1206.5856v1)). Keith Still's expert report said people died from lack of room to move, not from a stampede ([WSWS summary](https://www.wsws.org/en/articles/2013/06/15/love-j15.html), secondary). The site had one combined entry and exit ramp (widely reported; not re-checked in this session). | Not established in our sources. |
| **Mina, Hajj, 2015** | The official Saudi toll was 769 dead. An AP count from 36 countries found at least 2,411 ([Religion News / AP](https://religionnews.com/2015/12/10/saudi-arabia-stifled-true-death-count-hajj-stampede-2411/)). | Not covered in our sources. |
| **Astroworld, Houston, 2021** (10 died, [infonews](https://infonews.ca/?p=2446867)) | Weeks earlier, the NRG Park general manager raised concerns about the "ratio of guests to staff". Security lacked training and "devices necessary for rapid communication". No permits were obtained and no occupancy load was issued (Texas task force, [Insurance Journal](https://amp.insurancejournal.com/news/southcentral/2022/04/20/663892.htm)). | Houston police reported dangerous conditions from about 3:15 pm. The show went on for nearly 40 minutes after the mass-casualty declaration at 9:38 pm ([GMA timeline](https://www.goodmorningamerica.com/news/story/astroworld-festival-timeline-tragedy-unfolded-81036039)). |
| **Kanjuruhan, Malang, 2022** (135 died per Komnas HAM) | Police fired tear gas inside the stadium (about 45 rounds). Exits were locked or blocked and the stadium was over capacity ([Al Jazeera](https://www.aljazeera.com/news/2022/11/2/police-tear-gas-main-cause-of-indonesia-football-tragedy-report); [YLBHI coalition](https://ylbhi.or.id/informasi/siaran-pers/12-initial-findings-from-the-civil-society-coalition-fact-finding-team-regarding-human-rights-violence-incident-in-kanjuruhan-stadium/)). | Not covered in our sources. |
| **Itaewon, Seoul, 2022** | The first emergency call came at 6:34 pm, about 4 hours before the crush. Police released 11 calls and dispatched officers 4 times ([ADN/AP](https://www.adn.com/nation-world/2022/11/01/hours-before-first-deaths-in-seoul-crush-police-received-panicked-calls/)). Officials had discussed the night, but made no crowd plan despite internal warnings ([NST](https://www.nst.com.my/amp/world/region/2023/01/869875/police-south-korea-wrap-months-long-probe-into-itaewon-crush-23-officials)). A national commission's interim findings: 120 officers were on duty and none specialised in crowds ([Korea Times, Oct 2026](https://www.koreatimes.co.kr/southkorea/20261027/special-panel-seeks-to-question-ex-president-yoon-over-2022-crowd-crush)). Police-based estimates put density at up to 12 per m² ([Malay Mail](https://www.malaymail.com/news/world/2022/11/03/how-a-night-of-halloween-revelry-turned-to-disaster-in-south-korea/37340)). A simulation study peaked at 9.95 per m² ([PLoS ONE](https://pmc.ncbi.nlm.nih.gov/articles/PMC11244771)). | About 4 hours of calls. |
| **Hathras satsang, 2024** (121 died) | The SIT blamed organisers for inviting more people than permitted, and said police and administration did not take the event seriously ([The Week](https://www.theweek.in/news/india/2024/07/09/organising-committee-responsible-for-hathras-stampede-finds-sit-probing-mishap.html)). | Not covered in our sources. |
| **Tirupati token counters, Jan 2025** (6 died) | More than 4,000 people gathered ahead of 5 am token issue, then rushed the counters at once ([Business Standard](https://www.business-standard.com/india-news/tirupati-stampede-vaikunta-dwara-darshanam-tragedy-125010900303_1.html); [Tribune](https://www.tribuneindia.com/news/india/over-5000-devotees-rushed-for-tokens-what-caused-tirupati-stampede)). | People had been gathering since the previous morning. |
| **Maha Kumbh, Prayagraj, 29 Jan 2025** (at least 30 died) | The site had about 2,750 cameras, of which about 250 had AI ([Tribune](https://www.tribuneindia.com/news/uttar-pradesh/ai-based-surveillance-to-boost-safety-of-pilgrims-at-maha-kumbh)). An official said the AI cameras "were sending us timely alerts", but there were not enough police on the ground to stop the jam ([Context](https://www.context.news/ai/did-ai-fail-to-prevent-fatal-stampede-at-indias-kumbh-festival)). | The alerts existed; the response did not. |
| **New Delhi railway station, 15 Feb 2025** (18 died) | An RPF report reportedly blamed conflicting platform announcements and bottlenecks on foot overbridges 2 and 3 ([Zee](https://embed2.zeenews.com/india/rpf-report-reveals-lapses-behind-new-delhi-stampede-railways-denies-platform-mix-up-2860431.html)). The Railways denied any platform change ([Morung Express](https://morungexpress.com/new-delhi-station-tragedy-railways-denies-platform-change-or-special-train-cancellation)). The accounts are still contested. | Not established. |
| **Chinnaswamy, Bengaluru, Jun 2025** (11 died) | The commission found the stadium "unsuitable and unsafe for mass gatherings". Stakeholders went ahead knowing safe crowd management was impossible ([ESPNcricinfo](https://www.espncricinfo.com/ci/content/story/1496712.html); [South First](https://thesouthfirst.com/karnataka/karnataka-cabinet-accepts-dcunha-panel-report-on-chinnaswamy-stampede-rcb-ksca-police-officials-held-liable)). One report says 79 police were deployed for 2–3 lakh people ([Law Chakra](https://lawchakra.in/legal-updates/report-on-bengaluru-stampede-stadium), unverified). | Not established. |
| **Karur rally, Sep 2025** (41 died) | The leader arrived about 7 hours late. Turnout was about three times the expected 10,000. A police officer advised against arrival as the crowd grew restless ([The Week](https://www.theweek.in/news/india/2025/09/28/karur-tvk-stampede-organisational-lapses-lead-to-deadly-mishap-at-vijay-s-rally-death-toll-increases-to-40.amp.html)). | Hours, while the crowd waited. |

### What repeats

1. **The warning existed, and it did not become action.** Examples: Itaewon's calls, the Kumbh AI alerts, Astroworld's 40 minutes, and the Karur officer's advice.
2. **People were sent into a place that was already full.** Hillsborough's gate C and the New Delhi platform announcements are examples.
3. **Capacity was judged by eye, with no live count against a limit.** This happened at Hillsborough, Hathras, Bengaluru and Karur.
4. **Staff could not talk to each other or to the crowd fast enough.** The Astroworld task force named communications devices.
5. **"Who knew what, when" was fought over for years afterwards.** See the Hillsborough inquests, the Itaewon commission in 2026, and the New Delhi accounts.

---

## 2. The science in brief

### Density

Keith Still gives these levels ([gkstill.com, static density](https://www.gkstill.com/Support/crowd-density/CrowdDensity-1.html)):

- **Standing crowds:** 5 people per m² is "the UPPER limit for standing/viewing spaces". The UK Green Guide uses 4.7 per m².
- **Moving crowds:** UK event limits are typically assessed at 2 per m².
- **Moving queues:** 4 per m².

**Flow** rises with density until a critical density of 2–3 per m², then falls ([gkstill.com, Fruin](https://gkstill.com/Support/crowd-flow/fruin/Fruin1.html)). The Green Guide (6th edition) gives exit flow rates of 82 people per metre per minute on a level surface and 66 on stairs ([Greenwich paper](https://gala.gre.ac.uk/id/eprint/51608/1/51608%20HUNT_The_Impact_Of_Crowd_Composition_On_Egress_Performance_%28OA%29_2020.pdf); secondary, unverified against the guide). Experts commonly quote rising risk above 4 per m², and especially at 6 ([Malay Mail](https://www.malaymail.com/news/world/2022/11/03/how-a-night-of-halloween-revelry-turned-to-disaster-in-south-korea/37340)).

### Phases before a disaster

Helbing, Johansson and Al-Abideen studied video of the 2006 Jamarat Bridge crush ([arXiv physics/0701203](https://arxiv.org/abs/physics/0701203)). They found two sudden changes:

1. Smooth flow turned into stop-and-go waves.
2. Stop-and-go turned into "crowd turbulence".

Crowd pressure, defined as density times the variance of the velocities, rises before turbulence. Death and serious injury began **about 30 minutes after stop-and-go started and about 10 minutes after turbulence started**. The authors recommended automated video that alerts authorities "when the phase transitions occur" ([Physics World](https://physicsworld.com/a/crowd-turbulence-has-deadly-consequences/)).

### DIM-ICE

DIM-ICE is Still's planning tool. It sets three influences on crowd behaviour (Design, Information, Management) against normal and emergency modes ([gkstill.com DIM-ICE](https://www.gkstill.com/Support/WhyModel/dimice.html); [MMU paper](https://e-space.mmu.ac.uk/625559)). The "ICE" phases, Ingress, Circulation and Egress, come from Still's book and were not re-checked here. It tells us what our product covers:

- **Information:** announcements and signs.
- **Management:** stewards and responses.

Rulecam does both live. Design is out of scope.

### Signals that come before a crush

These are the signals the literature supports, and what we do with each today:

| Signal | Source | Rulecam today |
|---|---|---|
| Density rising for minutes with no outflow | Still | Measured |
| Speed falling to near zero while people keep arriving (stop-and-go) | Helbing et al. | Not yet detected |
| Rising crowd pressure | Helbing et al. | Measured |
| Counterflow and pushing | Jülich pushing detection reached 87% accuracy on entrance experiments ([arXiv 2302.08237](https://arxiv.org/pdf/2302.08237)) | Counterflow measured |
| One pinch point much denser than its neighbours | Still | Our densest-cell reading |

### Realistic lead time

From the events above, the build-up takes tens of minutes to hours:

- Itaewon: about 4 hours of calls.
- Jamarat: about 30 minutes from stop-and-go.
- Hillsborough: about 10 minutes or more after the pens were full.

The last minutes of compression are too late for anything except stopping inflow. Forecasting work is mostly seconds to minutes ahead:

- NEC said in 2016 that it forecast congestion 10 minutes ahead within a 20% margin of error ([NEC](https://www.nec.com/en/press/201610/global_20161024_05.html)).
- An academic paper forecasts density maps up to 6 seconds per step ([arXiv 2006.05127](https://arxiv.org/pdf/2006.05127)).

We found no paper that measured lead time before a real crush.

**What a realistic product promises:** "minutes, with a reason". Not "seconds before", and not "prediction".

---

## 3. Landscape

### Commercial products, programmes and consultancies

| Product | What it does | Price | Weakness for crush early warning |
|---|---|---|---|
| **Xovis** (3D ceiling sensors) | Queue length, wait time and throughput at airports: LaGuardia has 45+ sensors, and Atlanta and Luton also use it ([AviationPros](https://www.aviationpros.com/airport-business/airport-infrastructure-operations/press-release/21242582/xovis-ag-laguardia-airports-state-of-the-art-terminal-b-features-xovis-3d-sensors-and-software-to-reduce-queue-wait-times)) | Quote only. An aggregator estimates $25k–100k plus $10k–40k to implement ([SoftwareFinder](https://softwarefinder.com/facility-management-software/xovis), unverified) | Fixed ceiling install. It measures queue KPIs, not crush risk. No crowd announcements or steward loop. |
| **Veovo** | Passenger flow and forecasts from LiDAR, stereo cameras and WiFi/BLE, at 90–110+ airports ([Airports International](https://www.airportsinternational.com/node/164980); [Future Travel Experience](https://www.futuretravelexperience.com/2026/10/toronto-pearson-selects-veovo-for-next-generation-lidar-powered-passenger-flow-intelligence/)) | Enterprise, not disclosed | Built for airport operations, with its own sensors. Rail use is a vendor claim. |
| **Density.io** | Occupancy sensors for workplaces | $0.99 per sq ft per year, plus $299 per sensor ([one customer's rate card](https://density.io/pricing/cab2024)) | Offices, not crowds at 5 per m² or more. |
| **WaitTime** | Overhead-camera crowd and queue intelligence at stadiums, processed on site. Users include the MCG and the Denver Broncos ([DBusiness](https://www.dbusiness.com/daily-news/detroits-waittime-ai-solution-selected-by-denver-broncos/)) | Not disclosed | Concessions and revenue are the focus, not safety. |
| **Hikvision DeepinView** "Area Density Control" | Threshold alarm and voice prompt when occupancy passes a limit ([Hikvision](https://www.hikvision.com/us-en/newsroom/blog/2021/07/Watch-DeepinView-Cameras-in-Action--Manage-Overcrowding-with-Area-Density-Control)) | Built into the camera | One camera and one threshold. No forecast, no reasons, no verification. |
| **Axis Object Analytics** (crowd density scenario) and **AXIS People Counter** | Counts and density on the camera, with Data Insights dashboards ([Axis](https://axis.com/products/axis-object-analytics)) | Object Analytics is free on compatible cameras. People Counter is about $286–380 per licence ([CDW](https://m.cdw.com/product/axis-people-counter-license-1-license/4667868)) | Counts, not judgement. Nothing ties cameras into one site picture. |
| **Calipsa** (Motorola Solutions since 2022) | Cuts false intrusion alarms by 85–93% ([Calipsa FAQ](https://calipsa.io/faqs)) | Monthly fee per camera in volume tiers, not disclosed | Intrusion, not crowds. It shows that buyers pay to stop false alarms. |
| **Crowd Connected** | Location heatmaps from festival app users, used at 50+ festivals in 2017 ([Festival Insights](https://www.festivalinsights.com/2018/01/crowd-connected-festival-insights-infographic-2017/)) | Not disclosed | Only sees app users who opt in, so density comes from a sample. |
| **CrowdVision** (Skyfii in 2021, now Beonic) and **Springboard** (MRI Software, 2022) | Airport pedestrian analytics, and retail footfall ([Skift](https://skift.com/2021/07/23/australias-skyfii-targets-airport-tech-market-globally-with-its-computer-vision-acquisition/amp/); [The Grocer](https://www.thegrocer.co.uk/news/landlord-software-business-mri-acquires-footfall-data-firm-springboard/672086.article)) | Not disclosed | Operations and footfall, not safety. |
| **NEC crowd behaviour analysis** | Density, flow, falls and abnormal motion at Japanese stations, processed at the edge ([NEC](https://www.nec.com/en/global/solutions/safety/resourcecenter/download/crowd_detection.pdf)) | Enterprise | Large integrator projects. Current deployments unverified. |
| **Crowd Dynamics**, **Movement Strategies**, **Crowd Risk Analysis** (G. K. Still) | Consultancies: capacity, modelling, planning, expert witness ([Crowd Dynamics](https://www.crowddynamics.com/services); [Movement Strategies](https://movementstrategies.com/news/movement-strategies-are-delighted-to-be-awarded-innovation-in-safety-solutions-for-major-events)) | Consulting fees, not disclosed | Work happens before the event. Live monitoring is a project, not a product. |
| **Sawaher / Baseer** (Saudi Arabia, Hajj) | Density and counting from 5,000+ cameras at 80+ sites ([Arab News](https://www.arabnews.com/node/2603629/saudi-arabia); [Al Arabiya](https://english.alarabiya.net/News/saudi-arabia/2026/05/23/how-saudi-arabia-is-using-ai-to-transform-the-hajj-experience)) | Built by the government | Not available to anyone else. |
| **Kumbh ICCC** (Uttar Pradesh) and **Indian Railways** war rooms | Kumbh: AI density alerts. Railways: AI CCTV, holding areas and war rooms at 60–76 stations after Feb 2025 ([The Week](https://www.theweek.in/news/india/2025/02/17/delhi-ndls-stampede-horror-how-centralised-war-room-crowd-holding-zones-and-ai-will-help-monitor-mahakumbh-rush-at-railway-stations.html); [Business Standard](https://www.business-standard.com/amp/india-news/railways-station-crowd-control-measures-ashwini-vaishnaw-125030700840_1.html)) | Public programmes | At Kumbh the alerts fired but the response fell short. |
| **Vehant OKEAN** (India) | Density and congestion at the Karthigai Deepam festival ([case study](https://www.vehant.com/wp-content/uploads/2025/12/Q4-Case-Study-1.pdf)) | Not disclosed | Results are vendor-reported. |

### Academic and student systems

- **Jülich pushing detection:** 87% accuracy, near real time ([arXiv 2302.08237](https://arxiv.org/pdf/2302.08237)).
- **CrowdStop.AI:** a Berkeley capstone ([Berkeley](https://www.ischool.berkeley.edu/projects/2023/crowdstopai)).
- **KAIST congestion forecasting:** framed around Itaewon, KDD 2025 (press coverage only, unverified).

### Rules and guidance

- **UK Green Guide** (SGSA, 6th edition 2018): standing density 4.7 per m² and exit flow 82 per metre per minute. Both figures come from secondary sources above.
- **Purple Guide:** 2 per m² is commonly quoted for viewing areas ([East Herts plan](https://democracy.eastherts.gov.uk/documents/s70465/Appendix%20E%20Crowd%20Management%20Plan.pdf?J=54); unverified against the guide).
- **Martyn's Law** (Terrorism (Protection of Premises) Act 2025):
  - Royal Assent on 3 April 2025, with at least 24 months before the duties apply, so expected around spring 2027.
  - Standard tier covers 200–799 people. Enhanced tier covers 800 or more ([Brodies](https://brodies.com/insights/health-and-safety/martyns-law-receives-royal-assent-what-does-this-mean-for-your-business/); [Travers Smith](https://www.traverssmith.com/knowledge/knowledge-container/martyns-law-prepare-now-for-implementation-in-spring-2027/)).
  - Statutory guidance was published on 15 April 2026 ([Foot Anstey](https://www.footanstey.com/our-insights/articles-news/martyns-law-an-update-following-final-statutory-guidance/)).
  - It is a counter-terrorism duty, not a crush rule. It is relevant because it makes venues plan how they move and tell the public in an emergency (our reading; not legal advice).
- **India, NDMA 2014** "Managing Crowd at Events and Venues of Mass Gathering": calls for capacity planning, counting at staging points and constant monitoring. It is advisory and not binding ([Lawful Legal](https://lawfullegal.in/legal-framework-governing-stampede-disasters-in-india/?amp=1); [Vajiram](https://vajiramandravi.com/current-affairs/crowd-disaster-management/)). A Gujarat training note cites NCRB data that 79% of Indian stampedes happen at places of worship ([GIDM](https://gidm.gujarat.gov.in/sites/default/files/concept_note/ConceptNote%20Crowd%20managment%20training%204.pdf)).
- **Texas, after Astroworld:** the task force recommended a universal permitting template, security training and communications ([Insurance Journal](https://amp.insurancejournal.com/news/southcentral/2022/04/20/663892.htm)).

---

## 4. Voids, ranked

1. **Alerts that become action.** Kumbh had alerts but not enough people on the ground. Itaewon had 11 calls and 4 dispatches. At Astroworld the show ran 40 minutes after the declaration. No product we found closes the loop: alert, then a named steward, then an acknowledgement, then confirmation on camera, then escalation if nobody answers.
   - *Where we win:* Spark writes the staff actions. The notifier has reply buttons. `act/escalation.py` already has acknowledgement timers. The evidence chain records it all.
2. **A forecast with a reason, not a threshold alarm.** Hikvision and Axis fire when a count passes a number. The forecasting systems (Sawaher, Veovo, NEC) are government or enterprise projects. At Hillsborough capacity was judged by eye.
   - *Where we win:* each zone has a fitted trend with 2- and 5-minute projections, and Spark gives minutes to danger with the numbers behind it. Section 6 shows how to make this a physical fill clock.
3. **Instructions to the crowd that are safe, consistent and in its languages.** The New Delhi accounts point to conflicting announcements. At Hillsborough gate C sent people into full pens. Kumbh and Hajj crowds speak dozens of languages.
   - *Where we win:* Spark writes calm public-address text in any language. MMS-TTS covers 1,107 languages ([HF](https://huggingface.co/facebook/mms-tts)). Nobody we found checks that a redirect does not send people into another full zone.
4. **Pop-up and low-cost setups.** Rallies, satsangs, token counters and temporary melas will not install ceiling sensors. Xovis is quoted at an estimated $25k–100k (unverified).
   - *Where we win:* a phone camera paired by QR, and floor calibration from the site plan, MoGe-2 or the people themselves.
   - *Honest limit:* at shallow camera angles people hide each other and counts fall short (README).
5. **Verified alerts.** The market pays to cut false alarms (Calipsa claims 85–93% for intrusion). Crowd threshold alarms trip on occlusion and bad calibration.
   - *Where we win:* Glimmer checks the frames before anyone is alerted. The policy is safety-asymmetric: a verifier that is down never holds a warning back, and the alert says it went unchecked.
6. **Evidence for inquiries.** Hillsborough took decades. Itaewon is still being questioned in 2026. The New Delhi accounts conflict.
   - *Where we win:* every reading, assessment, check, notification and announcement goes into a hash-chained log.
7. **Crowd science for organisers who have no crowd scientist.** The consultancies work before the event, and NDMA guidance is advisory only.
   - *Where we win:* the operator gives a plain-language instruction in any language, and Spark reads a background of published reference values. The code contains no thresholds.

---

## 5. Open source to reuse now

### Counting models

These are a second opinion where SAM undercounts in dense, shallow-angle views. They can also feed count triangulation (C2 in `reports/2026-11-04-meta-features.md`). Licences were not checked.

| Model | What it is | Results |
|---|---|---|
| **P2PNet** | Point-based, gives positions as well as counts ([repo mirror](https://www.github.com/JusticeXu/CrowdCounting-P2PNet)) | Mean absolute error (MAE) 52.74 on ShanghaiTech Part A |
| **APGCC** | Point-based, ECCV 2024 ([github.com/AaronCIH/APGCC](https://relatedrepos.com/gh/AaronCIH/APGCC); [project page](https://apgcc.github.io)) | Not checked |
| **CLIP-EBC** | Density estimation built on CLIP ([arXiv 2403.09281](https://arxiv.org/html/2403.09281v3); code at github.com/Yiming-M/CLIP-EBC) | NWPU MAE 58.2 |
| **DM-Count** | Pretrained models for QNRF, NWPU and ShanghaiTech A/B, plus a Gradio demo ([Replicate](https://replicate.com/cvlab-stonybrook/crowd-counting/readme)) | Not checked |
| **CSRNet** | Older density model ([CommissarMa fork](https://github.com/CommissarMa/CSRNet-pytorch)) | MAE 67.74 on Part A |

### Data

- **Jülich Pedestrian Dynamics Data Archive:** trajectories from bottleneck, crowd-management and queue experiments, with DOIs ([data guidance paper](https://www.collective-dynamics.eu/index.php/cod/article/view/A141); ped.fz-juelich.de/da). We already score against it.
- **ShanghaiTech.**
- **UCF-QNRF:** 1,535 images, 1.25M points.
- **JHU-Crowd++:** 4,372 images, 1.51M points, including bad weather ([arXiv 2004.03597](https://arxiv.org/pdf/2004.03597)).
- **NWPU-Crowd:** 5,109 images, 2.13M points ([arXiv 2001.03360](https://arxiv.org/pdf/2001.03360)).

### Simulators

These are for what-if tests and for checking forecasts.

- **JuPedSim:** `pip install jupedsim`, a Python API, speed and force models, from Jülich IAS-7 ([PyPI](https://pypi.org/project/jupedsim)).
- **Vadere:** LGPL, with many locomotion models ([arXiv 1907.09520](https://arxiv.org/pdf/1907.09520.pdf)).
- A JuPedSim what-if step ("close gate A versus open gate B, density in 3 minutes") is a strong idea, but at about 4 h or more it falls outside the 3-hour limit.

### Pushing detection

- Jülich DL4PuDe and its cloud framework: optical flow plus a CNN, 87% accuracy ([arXiv 2302.08237](https://arxiv.org/pdf/2302.08237); [Zenodo](https://zenodo.org/records/8257076)).

### Speech

- **MMS-TTS:** 1,107 languages, CC-BY-NC 4.0 ([HF](https://huggingface.co/facebook/mms-tts)). We already use it.
- **Omnilingual ASR:** 1,600+ languages, Apache 2.0, from 300M to 7B parameters. The character error rate is below 10 for 78% of languages ([arXiv 2511.09690](https://arxiv.org/pdf/2511.09690); [OpenSourceForU](https://www.opensourceforu.com/2025/11/metas-omnilingual-asr-brings-1600-languages-to-open-source-speech-ai/)). Use it to transcribe each announcement back before it plays (C4 in the meta-features report).

---

## 6. Five feature specs for the crowd demo

Ranked by judge impact ÷ effort. Impact is scored 1–5 against the rubric: Impact 30%, Meta tooling 25%, Demo 25%, Technical 20%.

| # | Feature | Impact | Hours | Ratio |
|---|---|---|---|---|
| F1 | Route guard: never send people toward another full place | 5 | 2 | 2.5 |
| F2 | Steward loop: acknowledge, arrive, escalate | 5 | 2.5 | 2.0 |
| F3 | Fill clock: inflow, outflow and minutes to the limit | 4.5 | 2.5 | 1.8 |
| F4 | Incident record: what was known, and when | 3.5 | 2 | 1.75 |
| F5 | Phase meter: flowing, stop-and-go, turbulent | 4 | 3 | 1.3 |

### F1. Route guard: never send people toward another full place

**The problem.** The standard early action is "open another route" (it is already in the warden's `REFERENCE`). At Hillsborough the "other route", gate C, led into pens that were already full. At New Delhi in 2025, conflicting platform announcements are among the reported causes. Today Spark can say "redirect to gate 4" without anyone checking gate 4.

**Who pays:** multi-gate stadiums, station operators (Indian Railways war rooms at 60–76 stations), mela and temple administrations.

**What the user sees.** Every redirect on the alert card names its destination and the check on it:

- Passed: "Use Gate 4: checked, 1.2 per m² and falling (phone-104, 12 s ago)".
- Blocked: "Gate 4 also filling (3.8 per m², +0.6 per minute): redirect withdrawn, holding inflow at the outer cordon instead".

A small "site board" lists every watched zone with its latest density and trend.

**Which Meta models do what:**

- **Muse Spark 1.3** gets the site board in its prompt: every watched camera's latest zone readings and trends, with their age. It must state each redirect as a structured route (`to_zone`, `camera_id`, why).
- A deterministic guard checks each destination's latest reading. It checks freshness and that the destination is not rising toward the levels Spark itself cited. On a conflict it re-asks Spark once, with the conflict stated. No threshold is hard-coded: the guard only reports the facts, and Spark decides.
- **Glimmer** is asked to look at the destination's newest frame as well.
- **MMS-TTS** speaks only the approved route.

**Which module it extends.** `src/rulecam/crowd/warden.py`:

- Add `routes` to `CrowdAssessment`.
- Build the site board in the assessment prompt from `self._states` and the memory's latest readings.
- Add the guard before delivery.

`src/rulecam/crowd/live.html` gets the site board. `memory.py` already keeps the latest readings per camera.

**Effort:** about 2 h.

**Demo moment.** Two phones run two clips. Spark first proposes "send people to Gate B". The card flips to "Gate B is filling: changed to hold inflow". The Hindi and English announcements play the corrected instruction.

### F2. Steward loop: acknowledge, arrive, escalate

**The problem.** This is void 1, the most repeated finding in the inquiries: at Kumbh, Itaewon, Astroworld and Karur the warning existed and the response did not follow. Today the crowd warden calls `notifier.send` once. The acknowledgement timers in `act/escalation.py` (`EscalationManager`, buttons "On my way" / "False alarm" / "Escalate") are used by rule alerts, not crowd alerts.

**Who pays:** event organisers, police control rooms, and venue insurers, who need proof of duty of care.

**What the user sees.** The alert card shows a live response line:

- "Sent to gate stewards 18:11:30 · Ravi: On my way (14 s) · steward seen at the gate on camera 18:12:05".
- If nobody answers: "no answer in 60 s, escalated to supervisor and control room".

Each alert gets a "response time" figure.

**Which Meta models do what:**

- **Spark** splits its staff actions into assignments (role, zone, action, language).
- The **notifier** sends each assignment in the steward's language.
- Unacknowledged alerts escalate through `EscalationManager`. Spark's next assessment is told "no steward acknowledged", so it can raise the level or change the announcement.
- **SAM 3.1** counts a second concept, "person in high-visibility vest", inside the zone. This confirms a steward is actually there, using the existing `concept` path in `scan.py`.
- **Glimmer** confirms the arrival frame.

**Which module it extends.**

- `warden.py`: route the delivery step that today calls `notifier.send` through `EscalationManager.start`, and feed acknowledgement state into the assessment prompt.
- `scan.py`: a second concept count.
- The evidence chain already records the `action` records.

This overlaps with C3 in `reports/2026-11-04-meta-features.md`. This spec adds the on-camera arrival check and the feedback into Spark.

**Effort:** about 2.5 h.

**Demo moment.** A phone buzzes and a steward taps "On my way". About 30 s later a hi-vis vest appears at the gate and the card turns green: "steward on scene, seen on camera".

### F3. Fill clock: inflow, outflow and minutes to the limit

**The problem.** At Hillsborough capacity was judged by eye. Spark's `minutes_to_danger` is a judgement with no physical count behind it. Operators trust a number they can explain: people coming in per minute against people the exit can pass per minute.

**Who pays:** stadium safety officers working to the Green Guide, station operators, temple trusts.

**What the user sees.** Each zone shows a countdown, for example: "5 per m² in about 3 min 40 s: in 41 per minute, out 18 per minute". The clock reads "draining" when outflow exceeds inflow. A note flags when inflow exceeds what the gate can pass: width × the published 82 per metre per minute, shown as background, not as a rule.

**Which Meta models do what:**

- **SAM 3.1** counts, as today.
- **Optical flow** at the zone edge gives people per minute crossing in and out. This is deterministic.
- **Spark** gets the flows and the time to the reference levels alongside its fitted trend. It writes the forecast and decides the level.
- **Glimmer** checks the frames, as today.
- The clock is an input to Spark, never a trigger.

**Which module it extends.**

- `signals.py`: a new `boundary_flux(polygon, feet, velocities)` function. Also add `minutes_to(value)` on `Trend`, which already has `per_min`, `in_2_min` and `in_5_min`.
- `scan.py`: add `inflow_pm` and `outflow_pm` to `ZoneReading`.
- `warden.py`: add the flows to `_trends` and `_SERIES_FIELDS`.
- `live.html`: the countdown.

**Effort:** about 2.5–3 h.

**Risk.** The 64 s Jülich clip starts dense. A clip with a filling phase would show the clock better; Jülich has entrance and queue runs, but whether one fits was not checked.

**Demo moment.** The clock ticks down as the gate fills, then flips to "draining" when inflow stops. The scored truth is shown alongside.

### F4. Incident record: what was known, and when

**The problem.** Inquiries spend years rebuilding timelines:

- Hillsborough: the 2012 panel reviewed more than 450,000 pages.
- Astroworld: the police report runs to about 1,300 pages.
- Itaewon: a national commission is still questioning witnesses in 2026.
- New Delhi: the official accounts conflict.

**Who pays:** venue operators, insurers and licensing authorities.

**What the user sees.** One "Export incident record" button on an alert produces a single page:

- A minute-by-minute timeline of readings, Spark's level and why, Glimmer's verdict or why there was none, who was notified, the acknowledgements, and the announcements as text and audio.
- Each line carries its chain hash and shows its frame or heatmap.
- A seal: "chain verified, N records, no gaps".
- A section headed "What the system did not know". For example: "Glimmer had no GPU, sent unchecked", or "corridor zone extends past the floor points, density likely low".

**Which Meta models do what:**

- **Spark** writes the narrative using only the chained rows. Its structured output cites a record hash for every sentence, and any sentence that cites no hash is dropped.
- Chain verification is deterministic, so no model can change the record.
- The existing **Ask** agent answers follow-up questions over `crowd_assessments` and `crowd_alerts`.

**Which module it extends.**

- `warden.py`: reads its own `crowd_assessments` and `crowd_alerts` JSON-lines files.
- `rulecam.evidence.chain.EventChain`: verification.
- `src/rulecam/api/crowd.py`: a new `GET /crowd/alerts/{id}/record` route.

**Effort:** about 2 h.

**Demo moment.** After the Jülich run, the exported record says plainly "16:43:39 alert sent unchecked: Glimmer had no GPU", with the hash. Honesty is the selling point.

### F5. Phase meter: flowing, stop-and-go, turbulent

**The problem.** Density alone warns late. At Jamarat, death and serious injury began about 30 minutes after stop-and-go started, and the authors asked for video that detects these changes. We already compute crowd pressure, but not the stop-and-go stage: speed swinging between stop and go while density keeps rising.

**Who pays:** Hajj, mela and temple authorities, and stadium egress planners.

**What the user sees.** Each zone has a chip ("flowing", "stop-and-go since 18:04", or "turbulent") with a sparkline of speed over time. Spark cites it in its reason.

**Which Meta models do what:**

- **Optical-flow speeds** per zone and cell (existing) feed a deterministic stop-and-go index: how often speed swings between near zero and moving while density is high.
- **Pressure** (existing) indicates turbulence.
- **Spark** judges the phase.
- **Glimmer** gets the newest frame and the frame from 30 s earlier (existing `EARLIER_S`) and is asked "are people moving in stop-start waves?".

**Which module it extends.**

- `signals.py`: a new `stop_go(seconds, speeds)` function next to `motion` and `pressure`.
- `memory.py`: faster readings for the hottest zone.
- `warden.py`: add the index to `_SERIES_FIELDS`, and a line in `REFERENCE` that cites Helbing et al.

**Effort:** about 3 h.

**Risk.** It needs footage that shows stop-and-go. Whether the Jülich bottleneck runs do is unverified.

**Demo moment.** The phase chip turns amber ("stop-and-go") before the density peak. Spark's reason quotes the 2006 Jamarat finding.

---

## 7. Positioning lines

These never claim "first" and never use a disaster as a hook.

1. Rulecam turns the cameras and phones a venue already has into a crowd-safety watch. It measures people per square metre, says how many minutes are left, and tells stewards and the crowd what to do, in their own languages.
2. Inquiry after inquiry found that warnings existed but did not become action. Rulecam is built around the step after the alert: a checked warning, a named steward, a calm announcement, and a record of each.
3. Published crowd science as a live assistant: the numbers are measured, the judgement is Muse Spark's, and Muse Glimmer checks the frames before anyone is called.
4. For the temple queue, the station footbridge and the rally ground that will never buy a sensor network: a phone on a pole, one sentence of instruction, and a warning minutes ahead.
5. Every reading, judgement and announcement is hash-chained, so afterwards "what did we know, and when" has an answer nobody can edit.

**Best line:** number 2. It answers the most repeated finding in the inquiries (Kumbh, Itaewon, Astroworld), and our stack can show the whole sequence in one demo.
