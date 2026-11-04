# Rulecam market research: warehouses, logistics, manufacturing, construction

Researched 4 Nov 2026 for the Meta Global AI Developer Hackathon (submission due tonight). Segment: industrial and EHS (environment, health and safety). Every claim has a link. "Unverified" means only a third-party or low-quality source said it. "Vendor claim" means the company said it about itself and nobody independent checked.

**Short version.** The big AI-safety-camera vendors (Voxel, Protex, Intenseye) sell to large enterprises, hide their prices and publish only their own results. Video platforms (Spot AI, Ambient, Rhombus, Coram) already let you describe an alert in plain words, so "natural-language rules" alone is not new. Forklift systems sit on each truck or need everyone to wear a tag. What nobody sells to a 10–200 person site is this combination: near misses recorded without anyone having to report them, tied to the OSHA duty they trigger; warnings and training in the crew's language; rules tested before they go live; and evidence that a worker, a union rep or an insurer can check for themselves.

---

## 1. Landscape

Prices are almost never public in this market. That is a finding in itself: a small site can't even find out what it would pay.

### AI on existing cameras, built for safety (EHS)

| Product | What it does | Price | Weakness |
|---|---|---|---|
| **Voxel** | Runs AI on the site's existing security cameras to flag hazards and near misses ([TechCrunch](https://techcrunch.com/?p=2223881)). Added "Risk Insights" daily AI safety snapshots in Apr 2026 and an Executive Hub for multi-site views in Oct 2025 ([Voxel news](https://www.voxelai.com/news)). Says it uses no facial identification ([Benzinga](https://www.benzinga.com/content/27144216/voxel-raises-15m-to-decrease-workplace-injuries-and-prevent-workplace-accidents)). | Not published. TechCrunch removed its pricing at Voxel's request ([TechCrunch](https://techcrunch.com/?p=2223881)). A directory says about $3,000 a month per site (unverified, [thedailyworkflow](https://thedailyworkflow.com/service/Voxel)). | Built for enterprise and Fortune 500. Voxel's own head of customer solutions writes that worker trust is the main obstacle: workers who feel watched underreport near misses ([Forkliftaction, Dec 2025](https://www.forkliftaction.com/news/newsdisplay.aspx?nwid=31888)). |
| **Protex AI** | Privacy-preserving AI that plugs into existing CCTV in warehouses, factories and ports. Raised a $36M Series B in Feb 2025. Customers include M&S, P&G and DHL ([ThinkBusiness](https://www.thinkbusiness.ie/articles/protex-ai-workplace-safety-funding/), [TechRound](https://techround.co.uk/?p=74290)). | Not published. Sold through demos and AWS Marketplace private offers ([tooldirectory](https://tooldirectory.ai/tools/protex-ai)). | Claims a 64% risk reduction in three months; vendor claim with no independent check ([TechRound](https://techround.co.uk/?p=74290)). |
| **Intenseye** | Computer-vision safety platform. Strongest privacy defaults in the category: irreversible face blurring, 3D body anonymisation, and detection based on posture, never identity ([Intenseye privacy](https://www.intenseye.com/privacy)). Raised a $64M Series B in 2024 ([FinTech Global](https://fintech.global/2024/02/28/intenseye-raises-64m-in-series-b-to-drive-workplace-safety/)). | Quote only. G2 reviewers put its cost at the top of the scale, with about 2 months to implement and about 16 months to pay back ([G2](https://www.g2.com/products/intenseye/pricing)). | Slow payback and a heavy rollout for a small site. Privacy is a setting the employer controls; we found no worker-facing view of the rules. |
| **Everguard (Sentri360)** | Combines cameras, wearables and location signals for heavy industry. Customers are mostly steel mills: SeAH and Big River Steel ([CB Insights](https://www.cbinsights.com/company/everguard/customers)). | Not published. | Hardware and device management add complexity. Note the source is a competitor's (Voxel's) review ([Voxel](https://www.voxelai.com/industry-insights/everguard-ai-review)). |
| **Inviol** (NZ) | Computer vision for unsafe behaviour and PPE, with real-time coaching. Sold as subscription SaaS ([Caplight](https://www.caplight.com/company/inviol), [G2](https://www.g2.com/products/inviol/pricing)). | Not published. | Little public detail. |
| **CompScience** | Computer-vision safety bundled with workers' comp insurance. Acts as an MGA (managing general agent): Nationwide underwrites the policies and Swiss Re provides reinsurance ([Business Insurance](https://www.businessinsurance.com/compscience-launches-workers-comp-ai-agreement/)). | Bundled into the insurance premium. | Claims up to 23% fewer claims; vendor claim ([InsuranceNewsNet](https://insurancenewsnet.com/oarticle/compscience-launches-ai-powered-workers-comp-product-backed-by-nationwide-and-swiss-re)). Only works if you buy their insurance. |

### Video platforms with AI agents, built for security first

| Product | What it does | Price | Weakness |
|---|---|---|---|
| **Spot AI** | Camera management plus 15+ AI agents. **Iris** builds a custom detection from a plain-language request, with examples such as missing PPE and unsafely stacked pallets ([Spot AI Iris](https://www.spot.ai/iris), [launch coverage](https://www.artificialintelligence-news.com/news/spot-ai-introduces-the-worlds-first-universal-ai-agent-builder-for-security-cameras/)). Vendor claim: about 8 minutes and 20 images per detection. | About $99 per camera per month as a planning figure ([Safe and Sound](https://getsafeandsound.com/blog/spot-ai-pricing/)). | No published testing of a custom rule before it goes live. At $99 a camera, 20 cameras cost about $24k a year. |
| **Ambient.ai** | The Pulsar reasoning VLM (vision-language model). Users define custom notifications in natural language. Vendor claims up to 95% fewer false alarms; ServiceNow reported 94% ([Ambient press](https://www.ambient.ai/press/ambient-unveils-ambient-pulsar-reasoning-vlm)). | Not found. | Built for security operations centres, not EHS. The 95% is an "up to" figure. |
| **Rhombus** | ChatGPT-based natural-language search (Jan 2025) and "Insights" custom measurements ([Manila Times / GlobeNewswire](https://manilatimes.net/2025/01/30/tmt-newswire/globenewswire/rhombus-launches-new-ai-capabilities-for-faster-smarter-security-investigations/2047031)). | Professional licence $149 per camera per year, plus cameras from about $399. The Enterprise AI tier is reportedly $199 (unverified) ([Safe and Sound](https://getsafeandsound.com/blog/rhombus-pricing/)). | Needs its own hardware. Security first. |
| **Verkada** | Cameras with PPE detection and forklift and vehicle monitoring ([Verkada EHS](https://www.verkada.com/solutions/environment-health-and-safety/)). | Cloud licence from $249 per camera per year, plus hardware ([Safe and Sound](https://getsafeandsound.com/blog/verkada-cameras-price/)). | Hardware lock-in: every camera needs a licence. |
| **Coram** | Works with any ONVIF or RTSP camera. Offers natural-language search and alerts ([Coram vs Spot](https://www.coram.ai/post/spot-ai-vs-verkada)). | About $180–400+ per camera per year (unverified, [FitGap](https://us.fitgap.com/products/032483/coram-ai)). | Security first. |
| **Hakimo** | Remote guarding on existing cameras. | Estimated $1,500–3,000 a month for 10–20 cameras (unverified, [SoftwareFinder](https://softwarefinder.com/artificial-intelligence/hakimo)). | Security, not safety. |
| **VOLT.ai** | Alerts with a person reviewing them by default. Judges behaviour, not identity. | Published: $45–65 per camera stream per month ([VOLT pricing](https://volt.ai/pricing)). | Aimed at schools and security. |
| **Cogniac** | Industrial visual inspection: classify, detect, count and measure. | Price on request ([SAP Store](https://www.sap.com/germany/products/scm/partners/cogniac-corporation-cogniac-visual-operations-intelligence.html)). | Product quality, not people safety. |

### Forklift proximity systems (on the truck, or tag-based)

| Product | What it does | Price | Weakness |
|---|---|---|---|
| **Blaxtair** (Arcure) | 3D AI camera on the truck that tells people apart from objects ([Blaxtair](https://blaxtair.com/en/solutions/blaxtair-pedestrian-detection)). Burwell distributes it through 18 US branches ([Lift and Access](https://www.liftandaccess.com/article/burwell-material-handling-launches-ai-pedestrian-detection-system-for-forklift-safety)). | Dealer quote only. | Bought per truck, and it only sees what that truck sees: not round the end of a rack. |
| **ELOKON EloshieldAI** | UWB (ultra-wideband radio) plus tagless AI vision, with speed zones. Launched June 2025 ([Forkliftaction](https://www.forkliftaction.com/news/newsdisplay.aspx?nwid=31300)). | Not published. | Needs hardware on every truck and in the infrastructure. |
| **Linde Safety Guard** | UWB unit on the truck plus a tag or warning vest on each person, about 10 cm accuracy, with speed zones ([Linde](https://linde-mh.com/en/About-us/Innovations-from-Linde/Linde-Safety-Guard.html)). | Not published. | Everyone has to wear a charged tag. |
| **Litum PathAware** | UWB tags between vehicles plus tagless pedestrian detection. Per-vehicle subscription tiers (Core, IQ, IQ+) ([Newswire](https://www.newswire.com/news/litum-expands-pathaware-from-collision-warning-to-forklift-safety-intelligence)). | Not published. | Bought per vehicle. |
| **BEAST STAR Tech** | AI camera with three alert zones, driver monitoring and a DVR. No subscription ([Forkliftaction, sponsored](https://www.forkliftaction.com/news/newsdisplay.aspx?nwid=29684)). | "From $699" per truck. | Sponsored claims. Per truck. |
| **Slamcore Alert** | Add-on AI camera for vehicles, Oct 2025 ([Robotics & Automation News](https://roboticsandautomationnews.com/2025/10/24/slamcore-launches-new-ai-powered-safety-solution-that-turns-forklifts-into-safety-aware-machines/95865)). | Not found. | Per vehicle. |
| **ZoneSafe** (Cisco-Eagle) | RFID tag zones around vehicles ([Cisco-Eagle](https://www.cisco-eagle.com/zonesafe)). We found no product called "Zone Defense". | Not found. | Needs tags. |
| Category cost | A simple audible alarm costs under $100. Proximity and telematics systems cost thousands per vehicle, plus software and tags ([Racklify](https://racklify.com/encyclopedia/forklift-backup-alarms-vs-alternative-warning-systems-pros-cons-and-use-cases/)). | | |

Not found or not researched: "BlindSight" (no matching product found) and Toyota's systems (not researched in the time box).

### EHS software and wearables

| Product | What it does | Price | Weakness |
|---|---|---|---|
| **SafetyCulture** | Inspection checklists and incident reports. | Premium is $24 per seat per month on annual billing; free for up to 10 seats ([SafetyCulture pricing](https://safetyculture.com/pricing)). | Only records what people type in. G2 users complain that per-seat pricing gets expensive as headcount grows ([G2](https://www.g2.com/products/safetyculture-iauditor/pricing)). |
| **Benchmark Gensuite** | Enterprise EHS suite. | Not published ([G2](https://www.g2.com/products/benchmark-gensuite-ehs/pricing)). | Enterprise; no eyes on the floor. |
| **Cority** | Enterprise EHS+. | Priced to each customer's scope ([Cority](https://www.cority.com/pricing/)). | Enterprise. |
| **Kinetic Reflex** | Hip-worn sensor for lifting posture. It was given free to Kinetic workers' comp policyholders, then retired from that programme in Oct 2026 ([Insurance Journal](https://www.insurancejournal.com/news/national/2021/11/16/642468.htm), [Coverager](https://coverager.com/kinetic-retires-wearables-from-workers-comp-program/)). | Was bundled with insurance. | Every worker has to wear and charge a device. The insurer-backed version has now been withdrawn. |
| **StrongArm** | Ergonomics wearable. | About $22.50 per worker per month (unverified, [low-quality source](https://aihomebuilding.pages.dev/articles/ai-wearable-predict-construction-injury.html)). | Same wear-and-charge burden. |

### New entrants and hackathon near-twins

- **Hackathon and indie projects:** HazardVision on Devpost (real-time hazard video; [mirror](https://kelaenderkoe.pages.dev/Function-https-devpost.com/software/hazardvision)), DogWatch AI (PPE checks, AI Tinkerers Paris, Oct 2025; [entry](https://paris.aitinkerers.org/hackathons/h_FwbOwyDjzgU/entries/ht_KKIPcgT3Trs)), and a Product Hunt launch that turns CCTV into forklift, PPE and walkway monitoring ([Product Hunt](https://www.producthunt.com/posts/warehouse-safety-solution)).
- **Rules engines:** Sensfix sells a "Sens-Rule Engine" on top of CCTV ([Sensfix](https://kr.sensfix.com/?p=2778)).
- **Templated AI-vision sites:** iFactory, viAct and Visionplatform.ai sell pre-built forklift and PPE detectors.
- **Pattern across all of them:** fixed detectors plus zones. In what they publish, none tests a rule before it goes live, lets workers dispute an alert, or offers evidence a third party can check.

---

## 2. Pain points, with evidence

1. **False alerts and alarm fatigue**
   - In an r/Truckers thread titled "Anyone else getting tons of false alerts with these AI cameras?", 35 comments list Netradyne-style systems misreading speed signs, flagging phantom distraction and braking hard on overpass reflections ([snapshot](https://reddit.sentinel-team.org/posts/1qwxv0h/snapshots/2026-02-06T10%3A31%3A10.545875Z)).
   - SIERA.AI calls the forklift backup beep the most ignored noise on site. It says older proximity systems slow trucks for pillars and racks, so drivers look for ways around them ([SIERA](https://www.siera.ai/blog/beyond-the-bleep/)).
2. **Being watched, and footage used for performance management**
   - In an r/newzealand thread on a workplace AI camera, commenters ask about retention and whether footage trains models. One says their union agreement limits cameras to safety use, never performance management ([snapshot](https://reddit.sentinel-team.org/posts/1r33241/snapshots/2026-02-12T20%3A55%3A53.773532Z)).
   - France's CNIL fined Amazon's warehouse unit €32M for excessively intrusive monitoring ([Privacy International](https://privacyinternational.org/examples/warehouse-workers)).
   - St. Louis Amazon workers filed an NLRB charge over surveillance ([Labor Tribune](https://labortribune.com/st-louis-amazon-workers-file-unfair-labor-practice-charge-alleging-invasive-surveillance/)).
   - Under the EU AI Act, employers must inform workers' representatives before deploying high-risk AI for monitoring workers: Article 26(7) and Annex III point 4. The date it applies is disputed: 2 Aug 2026, or 2 Dec 2027 under the Digital Omnibus ([Garrigues](https://www.garrigues.com/en_GB/new/european-regulation-artificial-intelligence-and-industrial-relations), [Freshfields](https://technologyquotient.freshfields.com/post/102meia/ai-in-the-workplace-outlook-for-2026)).
3. **Hidden enterprise pricing**
   - Voxel, Protex, Intenseye, Inviol, Blaxtair, ELOKON and Litum publish no prices (sources in the tables).
   - Where figures exist, they run from about $99 per camera per month (Spot AI) to $1.5–3k a month for 10–20 cameras (Hakimo, unverified).
   - Intenseye's reviewers report about 16 months to pay back ([G2](https://www.g2.com/products/intenseye/pricing)).
4. **Language**
   - In 2024, Hispanic or Latino workers died at work at 4.3 per 100,000 full-time workers, against 3.3 for all workers. 68.5% of those who died were foreign-born ([BLS CFOI 2024](https://www.bls.gov/news.release/cfoi.nr0.htm)).
   - OSHA's 2010 policy: required training must be given in a language and vocabulary workers understand ([OSHA memo](https://www.osha.gov/laws-regs/standardinterpretations/2010-04-28); summary at [OH&S](https://ohsonline.com/articles/2010/04/29/osha-enforcement-memo-focused-on-latino-worker-safety.aspx)).
   - None of the camera vendors above advertises spoken warnings in the crew's language. This is our reading of their public pages (unverified).
5. **Alerts and results nobody can check**
   - Almost every outcome number in this market comes from the vendor itself: Ambient's "up to 95%", Protex's 64%, CompScience's 23% (links above).
   - A buyer, a union or an insurer has no way to re-check a single alert.
6. **Near misses go unreported**
   - One in four blue-collar workers saw a near miss in the past year and didn't report it. Frontline workers are almost twice as likely as managers to fear that reporting hurts their career (36% vs 19%) ([TRADESAFE survey of 503 workers](https://trdsf.com/blogs/news/workplace-safety-silence-reporting-risks)).
   - The often-quoted "over 60% don't report" has no primary source we could find (unverified, [SafetyNow](https://ilt.safetynow.com/near-misses-matter-building-a-culture-that-reports-and-learns-stats-and-facts/)).
7. **Tags and wearables get forgotten**
   - Tag-based forklift systems fail when a worker forgets their tag ([SIERA](https://www.siera.ai/blog/beyond-the-bleep/)).
   - Litum's newer product sells "pedestrians need no tag" as its headline benefit ([Newswire](https://www.newswire.com/news/litum-expands-pathaware-from-collision-warning-to-forklift-safety-intelligence)).
   - Kinetic withdrew its insurer-backed wearable ([Coverager](https://coverager.com/kinetic-retires-wearables-from-workers-comp-program/)).

---

## 3. The economics

| Fact | Number | Source |
|---|---|---|
| US forklift deaths and serious injuries | About 85 deaths and 34,900 serious injuries a year | [OSHA PIT directive](https://www.osha.gov/sites/default/files/enforcement/directives/reg1_fy2018_cpl-04-00-023e_powered-industrial-trucks.pdf) (as cited in our README) |
| Forklift deaths in 2024 | 84 | [NSC Injury Facts](https://injuryfacts.nsc.org/work/safety-topics/forklifts/) |
| How forklift deaths happen (NIOSH, 1980–94) | Overturns 22%, pedestrians struck 20%, crushed by forklift 16% | [NIOSH 2001-109](https://Cdc.gov/niosh/docs/2001-109/pdfs/2001-109.pdf) |
| Transportation and material-moving jobs, 2024 | 1,391 deaths. Fatality rate 12.5 per 100k, against 3.3 for all workers | [BLS CFOI](https://www.bls.gov/news.release/cfoi.nr0.htm), [BLS chart](https://www.bls.gov/charts/census-of-fatal-occupational-injuries/number-and-rate-of-fatal-work-injuries-by-occupation.htm) |
| OSHA's most-cited list, FY2026 (preliminary) | Powered industrial trucks (1910.178) rank 7th. Lockout/tagout is 3rd; machine guarding 10th | [OSHA Top 10](https://www.osha.gov/top10citedstandards) |
| OSHA maximum penalties, 2026 | $16,550 per serious violation. $165,514 per willful or repeat violation. $16,550 a day for failure to abate | [OSHA memo, 21 May 2026](https://www.osha.gov/memos/2026-05-21/2026-annual-adjustments-osha-civil-penalties) |
| Cost of one medically consulted work injury, 2024 | $48,000. One death: $1.54M. All injuries: $181.4B | [NSC](https://injuryfacts.nsc.org/work/costs/work-injury-costs/) |
| Direct workers' comp cost of serious injuries | $58.8B a year; "struck by object or equipment" alone is $5.81B, ranked 3rd | [Liberty Mutual WSI 2025](https://www.insurancejournal.com/magazines/mag-features/2025/08/04/834116.htm) |
| Vendor estimate for one forklift–pedestrian collision | About $45k direct, $80k for a 48-hour shutdown, $36k+ in legal and insurance costs (vendor claim) | [SIERA](https://www.siera.ai/blog/beyond-the-bleep/) |
| Insurers paying for camera safety | CompScience's MGA with Nationwide and Swiss Re. Intenseye partnered with AXA XL in 2021 (secondary source). **We found no published premium-credit schedule** | [Business Insurance](https://www.businessinsurance.com/compscience-launches-workers-comp-ai-agreement/), [FinTech Global](https://fintech.global/2024/02/28/intenseye-raises-64m-in-series-b-to-drive-workplace-safety/) |
| **The legal hook** | 1910.178(l)(4)(ii)(B): an operator needs refresher training when they have "been involved in an accident or near-miss incident". 1910.178(n)(4): slow down and sound the horn at cross aisles. 1910.178(m)(2): no one under raised forks | [OSHA 1910.178](https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.178) |
| Near-miss ratios | Bird's ratio is 1 serious : 10 minor : 30 property damage : 600 near misses, from about 1.7M reports. It is widely disputed | [Wikipedia: Accident triangle](https://en.wikipedia.org/wiki/Accident_triangle) |
| Which near misses matter | Only about 21% of recordable incidents could have caused a serious injury or fatality ("SIF potential"). Counting every near miss equally does not predict deaths | [DEKRA via COS](https://www.cos-mag.com/article/34740-1-in-5-incidents-has-potential-to-be-serious-or-fatal-speaker/), [ASSP](https://safetyfocus.assp.org/blog/preventing-serious-injuries-fatalities) |

**What this means for a 50-person warehouse:**

- One serious injury costs about $48k in economic cost. One serious OSHA citation costs up to $16.5k.
- A vendor charging $99 a camera a month costs about $12k a year for 10 cameras.
- Rulecam's pitch is not just "cheaper". It turns each near miss into the OSHA paperwork it legally triggers, and into a record an insurer can verify. That makes it a leading indicator instead of a vendor claim.

---

## 4. Voids, ranked

Ranked by how frequent and costly the problem is for a 10–200 person site, how weak today's affordable products are, and how well Rulecam's stack fits.

1. **Near misses become an OSHA duty, and the site never finds out.**
   - A near miss legally triggers refresher training under 1910.178(l)(4)(ii)(B), but a quarter of near misses are never reported (TRADESAFE).
   - Camera vendors detect near misses (Voxel lists it) but sell to enterprises. None that we found links a verified near miss to the clause it triggers, or separates the roughly 21% with SIF potential from the noise.
   - Rulecam already computes closest approach and time-to-collision (`rules/near_miss.py`) and verifies each alert with Glimmer.
2. **Trust is the main reason deployments fail, and the tools give workers nothing.**
   - Voxel's own staff say worker trust is the main barrier. Unions limit cameras to safety use. The EU AI Act requires informing worker representatives.
   - Intenseye blurs faces, but it is still a tool the employer controls.
   - Rulecam already has a worker QR page, one-tap and voice disputes, two-key privacy settings and a verifiable chain. Nobody else in the table offers this.
3. **Warnings and training in the crew's language, at the moment of risk, with no tags.**
   - The OSHA language policy and the 4.3 vs 3.3 fatality rates (above) make the case.
   - Truck-mounted cameras can't see round a rack; tag systems fail when tags are forgotten.
   - A fixed camera above a cross aisle sees both aisles. Rulecam already pre-renders spoken phrases with MMS-TTS in 30 languages (`voice_out/languages.py`).
4. **Insurer-grade evidence instead of vendor claims.**
   - Insurers clearly pay for camera safety data (CompScience, Nationwide, Swiss Re), yet we found no premium-credit schedule. Every outcome number in the market is self-reported.
   - Rulecam's hash chain, Merkle root, Wilson intervals and re-derivable decisions (`evidence/audit.py`) make a leading-indicator report that someone outside the company can check.
5. **Custom rules that are tested before they bother anyone.**
   - Spot AI Iris, Ambient and Rhombus already take plain-language requests, so that alone is not a gap.
   - The gap is everything after the request: attacking the rule with boundary scenes and mutants before it goes live (Rule CI), a second model on every alert, and leaving out the seated operator.
6. **Price.** About $99 per camera per month (or a quote-only enterprise contract) prices out the 10–200 person site. This void depends on Rulecam's per-camera-hour cost (`runtime/cost.py`), which we did not benchmark here.

---

## 5. Open-source and public assets to reuse now

| Asset | Use in Rulecam | Licence |
|---|---|---|
| [supervision](https://supervision.roboflow.com/latest/faq/) (`PolygonZone`, `LineZone`) | Cross-aisle line crossings for the blind-corner rule; quick zone overlays in the demo | MIT |
| [RF-DETR](https://roboflow.com/model-licenses/rf-detr) N/S/M/L | Fast fallback person and forklift detector if SAM 3.1 is cold | Apache-2.0 for those sizes; others use Roboflow's Platform Model License |
| [SH17 PPE dataset](https://github.com/ahmadmughees/SH17dataset): 8,099 images, 17 classes, YOLO labels | Rule CI hard negatives and an evaluation set for hi-vis and hard-hat rules | CC BY-NC-SA 4.0 per a third-party repo; images come from Pexels ([MYahya3](https://github.com/MYahya3/PPE-object-detection-dataset)). Fine for the hackathon, not for commercial use |
| [keremberke/forklift-object-detection](https://huggingface.co/datasets/keremberke/forklift-object-detection): 421 images, classes forklift and person | Extra forklift negatives and positives for Rule CI | CC BY 4.0 |
| [OmniFall](https://arxiv.org/pdf/2505.19889v1.pdf), Le2i ([Roboflow copy](https://universe.roboflow.com/new-workspace-qfcus/le2i-with-upright-and-fall)), UR Fall | Fall-detection scenes for a "person down" rule; we already ship RTMPose | Varies by dataset; Roboflow Le2i is CC BY 4.0 |
| [Coverr forklift clips](https://coverr.co/stock-video-footage/forklifts) and Pexels | Demo footage; Coverr needs no credit | Coverr is royalty-free. Pexels allows commercial use, but clips with recognisable people may lack model releases ([licenseorg](https://licenseorg.com/guide/video/pexels-video)) |
| Frigate NVR | RTSP ingest from cheap cameras | Believed to be MIT (unverified) |

Advice: don't add a new detector tonight. SAM 3.1 plus Glimmer already covers detection. Use these datasets as Rule CI hard negatives and evaluation clips, which is what the judges' "proven before live" story needs.

---

## 6. Five feature specs for the warehouse demo

Ranked by judge impact ÷ effort (impact scored 1–10, effort in hours).

### F1. "Near miss → refresher training due" (impact 9, effort 2.5 h, ratio 3.6)

- **Problem and who pays.**
  - Under 1910.178(l)(4)(ii)(B), any near miss legally requires refresher training, but small sites never hear about most near misses.
  - Powered industrial trucks are 7th on OSHA's most-cited list, at up to $16,550 per serious citation.
  - The owner or ops manager pays to avoid citations and injuries; the insurer's loss-control team wants the record.
- **What the user sees.**
  - A forklift passes a walker. The alert card reads: "Near miss: 0.8 m, 1.1 s to contact. Could have been serious: yes. OSHA 1910.178(l)(4)(ii)(B): refresher training due for the operator of truck FL-2."
  - The owner taps "Training done (role: shift lead)", and that tap is chained.
  - The worker poster shows "3 near misses at cross aisle B this week", with no names.
- **Meta models.**
  - SAM 3.1 tracks and measures.
  - Glimmer verifies the alert and reads the truck's painted unit number. Its `scene_text` field already copies visible text (`verify/tier1.py`), so we identify the truck, never a face.
  - Muse Spark writes the plain-language note. The clause quote goes through the existing citation check (`planner/standards.py`), which fetches the OSHA page and matches the quote.
  - Code alone (`NearMiss.closest_m`, `ttc_s`) decides whether it could have been serious, e.g. closest approach under 1 m and time to contact under 2 s.
- **Module it extends.**
  - `rules/near_miss.py`: today it only annotates the trace of a fired `distance_m` rule.
  - New code: a `near_miss` label record in the chain, plus the missing `near_miss_events` DuckDB writer. The `near_miss_per_forklift_hour` metric in `advisor/metrics.py` is already defined but has no table to read from.
  - `act/escalation.py` carries the "training due" task until someone acknowledges it.
  - `evidence/incident.py` prints the clause.
- **Demo moment (10 s).** The close pass plays, the card pops with the OSHA clause, and the owner taps Done. The verify page shows the record chained.

### F2. Forklift evidence pack for OSHA and insurers (impact 7, effort 2 h, ratio 3.5)

- **Problem and who pays.**
  - Insurers pay for camera safety data, but nothing in the market is independently verifiable, and we found no premium-credit schedule.
  - At workers' comp renewal, the owner or their broker needs leading indicators an underwriter can believe.
- **What the user sees.** A "Download forklift pack" button produces a 2-page PDF with:
  - the clauses up front: 1910.178(m)(2), (n)(4) and (l)(4)(ii)(B);
  - near misses per forklift-hour, and the share of time each camera actually watched, with Wilson 95% intervals;
  - alert precision (Glimmer agreement, disputes upheld);
  - corrective actions and refresher trainings completed;
  - a QR code that recomputes the Merkle root in the browser.
- **Meta models.**
  - Code computes every number. This is the advisor's rule: no model writes a figure.
  - Muse Spark writes the summary paragraph around those numbers.
  - Glimmer back-translates the summary for posting in the crew's language.
- **Module it extends.** `evidence/packs.py`: add `osha_pit` to `TemplateName`, `LEAD_CLAUSES`, `TITLES` and `INTROS`, next to `food_code` and `f689`. It reuses `wilson_interval`, `allowed_thumbnails` and `qr_data_uri`, plus the metric from F1.
- **Demo moment.** Hold the PDF up to the camera, scan the QR with a phone, and watch the hashes turn green.

### F3. Speak-up: report a near miss by voice, in any language (impact 9, effort 3 h, ratio 3.0)

- **Problem and who pays.**
  - One in four workers sees a near miss and stays silent, mostly for fear of blame. Hispanic workers die at a 30% higher rate.
  - Near-miss capture is the leading indicator owners and insurers want, and workers finally get a voice.
- **What the user sees.**
  - A worker scans the poster QR, holds the button and speaks Spanish ("a forklift almost hit me in aisle 3 ten minutes ago"). The audio is discarded.
  - Within about 15 s the owner sees: "Worker report confirmed by camera 2: 0.7 m at 14:02. No rule covers aisle 3. Proposed rule: keep people 2 m from a moving forklift in aisle 3. Run Rule CI?"
- **Meta models.**
  - Voice Transcribe turns speech into text.
  - Muse Spark extracts where, when and what into a typed object.
  - Code re-runs `near_miss` over the stored SAM 3.1 tracks in a window of plus or minus 2 minutes. No new model calls.
  - Glimmer verifies the matching still.
  - Muse Spark drafts the rule through the existing planner, and Rule CI tests it.
- **Module it extends.**
  - `worker/api.py`: add a `POST /report/voice` next to `/dispute/voice`.
  - `worker/disputes.py`: reuse `transcribe_and_discard_audio`, `DailyLimiter` and the abuse bounds.
  - `rules/backtest.py` reads the stored tracks.
  - The planner pipeline produces the draft rule. The report is chained anonymously, like a dispute.
- **Demo moment.** A Spanish voice note, then a camera-confirmed measurement, then a new rule proposed and attacked by Rule CI, all on screen in under 30 s.

### F4. Crew-rep "safety-only" certificate (impact 6, effort 2 h, ratio 3.0)

- **Problem and who pays.**
  - Deployments stall on worker trust, union clauses limit cameras to safety use, and the EU AI Act requires informing worker representatives.
  - The owner pays to get sign-off from the crew or union rep quickly.
- **What the user sees.** A "Crew rep" tab on the worker page, with a monthly certificate in the crew's language. It shows:
  - the protected settings (`anonymous_mode`, `retention_days`, `no_individual_metrics`), and either "unchanged" or "changed with both keys on [date]";
  - how many alerts there were, how many were disputed and upheld, and how many became permanent tests;
  - that retention deletions were done, and that the chain is intact.
- **Meta models.**
  - Code builds every line.
  - Glimmer translates the certificate.
  - Spark Open, once its key works, reviews the text for anything that identifies a person.
- **Module it extends.**
  - `worker/two_key.py` provides the `TwoKeySettingsManager` history.
  - `worker/log.py` reads disputes and outcomes from the chain.
  - `rule_ci/hard_negatives.py` counts the permanent tests.
  - Rendering uses a new `crew_report` template in `evidence/packs.py`.
- **Demo moment.** "The union rep scans this in Polish and sees nobody shortened retention without their key."

### F5. Blind-corner bilingual warning, no tags (impact 8.5, effort 3 h, ratio 2.8)

- **Problem and who pays.**
  - 1910.178(n)(4) requires drivers to slow and sound the horn at cross aisles. NIOSH puts pedestrians struck at 20% of forklift deaths.
  - Truck cameras can't see round racks, and tag systems need everyone to wear a tag.
  - The owner pays, as a cheaper alternative to per-truck hardware.
- **What the user sees.**
  - The owner says, in Spanish or English: "warn people at the dock 3 corner when a forklift is coming, in Spanish and English."
  - The site speaker calls out "¡Montacargas acercándose! Forklift coming" before the two meet.
  - Separately, the system logs "did not slow at cross aisle" when the forklift's speed stays above 1.5 m/s inside the corner zone.
- **Meta models.**
  - Muse Spark compiles the request into a Rule IR (`all(inside(person, aisle_A), inside(forklift, aisle_B), moving(forklift, speed_mps_gt=1.5))`).
  - SAM 3.1 tracks the person and the forklift.
  - The rule is HIGH severity, so the speaker fires first and Glimmer verifies afterwards (`verify/policy.py`).
  - Glimmer pre-translates the phrases, and MMS-TTS speaks them, cached at plan approval (`voice_out/prerender.py`).
- **Module it extends.**
  - `planner/site_config.py`: the owner draws the two aisle polygons.
  - `rules/predicates/spatial.py`: the `inside` and `moving` predicates with `speed_mps_gt` already exist.
  - `act/devices.py` drives the `Speaker`.
  - `rule_ci/boundary_pairs.py` adds the "person 1 cm outside the zone" scene.
  - New scenario fixture: `scenarios/warehouse_corner.yaml`.
- **Demo moment.** A split screen of two aisles that can't see each other, and the camera that sees both. The bilingual warning plays, and the person stops.

**Alternate, if Muse Code is the story to show:** a "no one under raised forks" rule type for 1910.178(m)(2), written by Muse Code with a Z3 proof (`extensions/`). Judges would love it, but whether a single camera can measure fork height is uncertain, so it's riskier than 3 hours.

---

## 7. Positioning against competitors

1. **Against Spot AI Iris, Ambient and Rhombus:** "They let you describe an alert; Rulecam attacks your rule with edge cases and broken copies before it can go live, then a second model checks every alert."
2. **Against Voxel, Protex and Intenseye:** "Same cameras you already own, no sales call: rules in your crew's language, and results anyone can re-check instead of vendor-reported ROI."
3. **Against Blaxtair, Linde Safety Guard and Litum:** "A camera on the ceiling sees round the rack a truck camera can't, with no tag to forget or charge."
4. **Against SafetyCulture and EHS software:** "Checklists record what people remember to report; Rulecam records the near misses nobody reported, and tells you which OSHA duty each one triggers."
5. **For workers and unions:** "Your crew can read every rule, dispute every alert in their own language, and verify every record; a false alarm becomes a permanent test, and no face is ever recognised."

**Best line:** "Other safety cameras ask you to trust their numbers; Rulecam proves every rule before it goes live and lets the crew check every alert."
