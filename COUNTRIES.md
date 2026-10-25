# Country Guide: where einvoice_erpgulf fits, country by country

**Intended for:** ERPGulf developers, partners and implementers deciding whether a country can run on `einvoice_erpgulf`, what its profile needs, and how much work the port is. For the mechanics of writing a profile or an adapter, see the [Developer Guide](DEVELOPER_GUIDE.md); this document is about the countries themselves.

**Accuracy note.** Mandate dates, profile names and identifier schemes below are stated as known in mid-2026 and are the kind of facts that move. Before the first customer in a country, confirm every constant in that country's section against the tax authority's current technical specification and the Peppol authority's published profile. Where a value is a working assumption rather than a published fact it is marked **(verify)**.

---

## 1. The three ingredients that make a country a fit

`einvoice_erpgulf` is built for one model: the taxpayer chooses a **provider** (an access point, an accredited service provider, a certified platform), the ERP sends the invoice through that provider, and the provider delivers it to the buyer and, where required, to the tax authority. How well a country fits comes down to three ingredients:

| Ingredient | What it means | Where it lives in the app |
| --- | --- | --- |
| **A provider layer** | The taxpayer connects to a provider, not directly to a government endpoint. Several providers compete; the customer may switch. | `core/base.py` (the adapter contract), `countries/<cc>/providers/` |
| **A structured document standard** | UBL (or CII) with a published semantic model: Peppol PINT, Peppol BIS 3.0 / EN 16931, or a national specialisation of one | `core/standards/`, `countries/<cc>/profile.py` |
| **A status lifecycle** | The provider reports back: delivered, reported to the authority, rejected, failed | `core/status_sync.py`, `custom_reporting_status`, the ribbon |

A country with all three ports as a profile plus adapters. A country missing the first — a single government endpoint every taxpayer must integrate with directly — is a **clearance platform** and belongs in a separate app (that is the ZATCA model). A country missing the second (unstructured PDF or EDI only) is not e-invoicing in this sense.

### The tiers used below

- **Tier A — exact copy of the UAE.** Peppol 5-corner with the tax authority as corner five, a PINT profile, government-accredited providers. Port: profile constants, register existing or new adapters.
- **Tier B — Peppol with a profile.** Peppol network, but 4-corner (no tax corner yet) and/or open access-point certification instead of government accreditation, and/or BIS 3.0 instead of PINT. Port: profile plus adapters; European access points take UBL XML, so `core/standards/ubl` is used.
- **Tier C — provider model, bigger profile.** Accredited platforms like the UAE, but a national document format or lifecycle that needs more than a profile (France's e-reporting, Germany's XRechnung extensions). Port: profile plus new common features.
- **Tier D — clearance platform.** One government endpoint, no provider to choose. Not this app.

---

## 2. Countries shipped in the app

### United Arab Emirates — Tier A, in production

| | |
| --- | --- |
| Authority | Federal Tax Authority (FTA) |
| Model | DCTCE: Peppol 5-corner, Continuous Transaction Controls; the ASP reports to the FTA as corner five |
| Standard | PINT-AE (`urn:peppol:pint:billing-1@ae-1`, self-billing `urn:peppol:pint:selfbilling-1@ae-1`) |
| Providers | FTA-accredited Service Providers (ASPs); the FTA publishes the list; the customer must use one |
| Identifier | Peppol scheme **0235**, endpoint ID = the 10-digit TIN = first 10 digits of the 15-digit TRN |
| Tax | VAT 5 %; categories S, Z, E, AE (reverse charge), O, N (margin scheme) |
| Regions | Emirate required on domestic addresses (IBR-144-ae): AUH, DXB, SHJ, AJM, UAQ, RAK, FUJ |
| Currency | AED, 2 decimals |
| Documents | 380 invoice, 381 credit note, 389 self-billed invoice, 396 self-billed credit note; UAE-specific BTAE fields (transaction type flags, legal registration type, discrepancy codes) |
| Mandate | Phased B2B/B2G rollout from 2026 by taxpayer size **(verify current phase)** |
| In the app | `countries/ae/profile.py` (AeProfile), 30 UAE-only custom fields, adapters for Flick, Marmin, Suntech (Fynamics in tree, not registered) |

What makes the UAE the reference: every ASP builds the UBL itself from a JSON the ERP sends, so the adapters send JSON and never touch XML. That is also why other PINT countries whose providers accept JSON are the easiest ports, and why European access points (which take the UBL directly) need the builder in `core/standards/ubl`.

### Oman — Tier A, profile ready

| | |
| --- | --- |
| Authority | Oman Tax Authority (OTA) |
| Model | Peppol 5-corner with accredited service providers, modelled on the UAE |
| Standard | PINT-OM **(verify suffix; the profile assumes `om-1`)** |
| Providers | OTA-accredited service providers; the first are expected to be the same companies as in the UAE |
| Identifier | VATIN; the profile assumes scheme 0235 and the first 10 digits as the endpoint **(verify both — the OTA may register its own ICD)** |
| Tax | VAT 5 %; categories as PINT default (S, Z, E, AE, O, G, K) |
| Regions | Governorates (ISO 3166-2:OM codes MA, ZU, MU, BU, DA, BS, BJ, SS, SJ, ZA, WU); whether a domestic address must carry one is **(verify)**; the profile defaults to not required |
| Currency | OMR, **3 decimals** (baisa) — the one place the shared `money()` helper is profile-aware |
| Mandate | Phased from 2026 **(verify dates)** |
| In the app | `countries/om/profile.py` (OmProfile) with the four constants marked TODO at the top, Governorate field on Address; providers block empty — list a UAE adapter class under `"OM"` once its Oman API is confirmed identical |

Porting guidance: confirm the four constants, register the adapters, then run the Testing Guide section 9 on the OTA sandbox. Expect the UAE-only BTAE fields (transaction type flags, FZ beneficiary, RCM nature codes) not to apply; the Oman profile does not install them.

### Belgium — Tier B, profile ready

| | |
| --- | --- |
| Authority | FPS Finance (SPF Finances / FOD Financiën) |
| Model | Peppol 4-corner, mandatory B2B exchange since **1 January 2026**; a fifth corner (near-real-time reporting to FPS Finance) planned for **2028** |
| Standard | Peppol BIS Billing 3.0 on EN 16931 (`urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0`) |
| Providers | Any Peppol-certified access point; no government accreditation list |
| Identifier | Scheme **0208** (KBO/BCE enterprise number, 10 digits with leading 0); VAT number = BE + the same digits |
| Tax | VAT 21 / 12 / 6 %; EN 16931 categories S, Z, E, AE, K (intra-EU), G (export), O; VATEX exemption reason codes |
| Regions | None required |
| Currency | EUR, 2 decimals; piece unit C62 |
| Documents | 380 / 381; BIS 3.0 requires a buyer reference or order reference (`validate_document` enforces it) |
| Status flow | Ends at "delivered" (buyer's access point has the document) until 2028 — `final_reporting_statuses` includes it |
| In the app | `countries/be/profile.py` (BeProfile), Buyer Reference field on Sales Invoice; providers block empty |

Porting guidance: an access-point adapter posts `self.build_ubl(doc)` (bytes) to the access point's API and maps its delivery statuses to `delivered` / `rejected` / `failed`. Most Belgian access points also offer inbound delivery via webhook or polling; `parse_incoming_invoice` reads the UBL they hand back (a UBL reader in `core/standards/ubl` is the next shared piece to write when the first Belgian customer needs purchase-side import).

---

## 3. Tier A candidates — the UAE model elsewhere

### Singapore

| | |
| --- | --- |
| Authority | IRAS (tax), IMDA (Peppol authority) |
| Model | InvoiceNow: Peppol 5-corner, IRAS as corner five via GST InvoiceNow Requirement |
| Standard | PINT-SG (`@sg-1`) **(verify suffix)** |
| Providers | IMDA-accredited InvoiceNow access points |
| Identifier | Scheme **0195** (Singapore UEN); endpoint ID = UEN |
| Tax | GST 9 %; categories SR (standard), ZR (zero), ES (exempt), OS (out of scope), TX etc. — map to UNCL5305 S / Z / E / O in the profile |
| Regions | None |
| Currency | SGD, 2 decimals |
| Mandate | GST-registered businesses in phases: voluntary from May 2025, new voluntary registrants Nov 2025, new mandatory registrants Apr 2026 **(verify current stage)** |
| Port | Profile only (complete from public specs); adapters per access point. Closest to the UAE in design and older than it |

### Malaysia — Peppol leg

| | |
| --- | --- |
| Authority | LHDN / IRBM (tax), MDEC (Peppol authority) |
| Model | Two routes: direct clearance through **MyInvois** (LHDN's API — the route most taxpayers and ERPGulf's existing Malaysia app use), and the Peppol leg via MDEC-accredited access points with PINT-MY, 5-corner |
| Standard | PINT-MY (`@my-1`) **(verify)** |
| Identifier | Scheme **0230** (Malaysian TIN) **(verify)** |
| Tax | SST (sales tax 10 %, service tax 8 %) rather than VAT; tax categories are national — a profile mapping is needed |
| Currency | MYR, 2 decimals |
| Mandate | Phased by turnover since Aug 2024, all taxpayers by 2025/2026 **(verify)** |
| Port | Profile plus access-point adapters for customers who use the Peppol route; the MyInvois-direct route stays in the separate Malaysia app (Tier D). Treat as Tier A "with an asterisk" |

### Bahrain

| | |
| --- | --- |
| Authority | National Bureau for Revenue (NBR) |
| Model | E-invoicing announced; technical standard not yet published; the regional expectation is the UAE/Oman model **(verify before any work)** |
| Tax | VAT 10 % |
| Currency | BHD, 3 decimals |
| Port | Likely a Tier A profile once the spec lands; nothing to build yet |

### Other Gulf states

Qatar and Kuwait have announced or are studying e-invoicing; no published technical model. Saudi Arabia is Tier D (see below).

---

## 4. Tier B candidates — Peppol with a profile

### Australia and New Zealand

| | |
| --- | --- |
| Authority | ATO (AU), MBIE (NZ) as Peppol authorities; no tax corner |
| Model | Peppol 4-corner; mandated for government buyers, voluntary B2B (AU is moving toward a B2B mandate **(verify)**) |
| Standard | PINT A-NZ (`@aunz-1`) **(verify suffix)** |
| Identifier | Scheme **0151** (ABN) for Australia, **0088** (GLN) or NZBN **0225** for New Zealand **(verify)** |
| Tax | GST 10 % (AU), 15 % (NZ) |
| Currency | AUD / NZD |
| Port | Profile only; status ends at delivered |

### Japan

| | |
| --- | --- |
| Authority | Digital Agency (Peppol authority); NTA for the Qualified Invoice System |
| Model | Peppol 4-corner, voluntary, driven by the Qualified Invoice System (2023) |
| Standard | JP PINT (`@jp-1`) **(verify)** |
| Identifier | Scheme **0221** (Japanese registered invoice issuer number, "T" + 13 digits) **(verify)** |
| Tax | Consumption tax 10 % / 8 % reduced; JP PINT has its own tax-category rules |
| Currency | JPY, **0 decimals** — `currency_decimals = 0` |
| Port | Profile only; Japanese-language labels in the country fixtures |

### Netherlands

Peppol BIS 3.0; B2G mandatory since 2017, B2B voluntary and widespread; scheme **0106** (KvK) or **0190** (OIN) for government; EUR. No national extension. Profile is BeProfile with the identifiers changed.

### Nordics — Denmark, Norway, Sweden, Finland

Peppol BIS 3.0 everywhere, with national conventions: Denmark (NemHandel plus Peppol, scheme **0184** CVR, mandatory digital bookkeeping law phasing in), Norway (EHF 3.0 = BIS 3.0 with Norwegian rules, scheme **0192** org number, B2G mandatory), Sweden (scheme **0007** org number, B2G mandatory), Finland (scheme **0212** business ID, Finvoice also common). EUR / DKK / NOK / SEK. Profile per country, each a short subclass of EN16931Profile.

### Ireland

Peppol BIS 3.0, B2G; B2B mandate under consultation under the EU ViDA timeline **(verify)**; scheme **0196**? **(verify — Irish identifiers vary)**; EUR.

### Germany — Tier B/C

| | |
| --- | --- |
| Authority | BMF / state portals; KoSIT publishes XRechnung |
| Model | Mandatory to **receive** EN 16931 e-invoices since Jan 2025; issuing mandatory in phases 2027–2028. No network is mandated: Peppol, email and portals all count |
| Standard | EN 16931 in two national flavours: **XRechnung** (UBL or CII, with German extensions such as the Leitweg-ID for public buyers and stricter payment/contact rules) and **ZUGFeRD** (CII embedded in a PDF/A-3, Factur-X compatible) |
| Identifier | Scheme **0204** (Leitweg-ID) for public buyers; **9930** (VAT number) widely used for B2B **(verify)** |
| Tax | VAT 19 / 7 % |
| Port | Profile plus two things the UAE never needed: the UBL builder must produce the XRechnung extension fields, and many customers want "generate the XML and email it / upload it" with no access point — that is still an adapter (one whose `submit_invoice` writes the file and whose status is always `issued`). ZUGFeRD needs a CII writer and PDF/A-3 embedding: `core/standards/cii`, not yet written |

### France — Tier C

| | |
| --- | --- |
| Authority | DGFiP |
| Model | Mandatory from **September 2026** (receiving for all, issuing for large/mid), **2027** for small; through accredited platforms (PA, *plateformes agréées*, formerly PDP) — the "Y model". The provider layer is exactly the UAE's idea |
| Standard | EN 16931 in UBL, CII or **Factur-X** (PDF/A-3 with CII); Peppol is an interoperability option between platforms, not the backbone |
| Identifier | SIREN / SIRET; scheme **0009** (SIRET) or **0002** (SIREN) **(verify which the PA expects)** |
| Tax | VAT 20 / 10 / 5.5 / 2.1 % |
| What is new | A mandated **lifecycle** (deposited, rejected, refused, received, approved, paid …) the platform reports and the ERP must store; **e-reporting** of B2C and cross-border transactions and of payment data for services; the Factur-X PDF format |
| Port | Profile plus a `countries/fr/` module for lifecycle statuses and e-reporting; CII writer shared with Germany. The adapter pattern fits; the scope is roughly half the body new |

### Other EU countries on the ViDA path

Under the EU "VAT in the Digital Age" package, intra-EU B2B e-invoicing on EN 16931 becomes mandatory from **2030**, and member states may mandate domestic B2B earlier without derogation. Countries already moving: **Croatia** (Fiskalizacija 2.0, 2026, clearance plus Peppol), **Latvia**, **Slovenia**, **Slovakia**, **Estonia** (receiving on request since 2025), **Greece** (myDATA reporting plus Peppol for B2G, B2B mandate phasing from 2026 **(verify)**). Each is a BIS 3.0 profile with a national identifier scheme; the clearance-flavoured ones (Croatia, Greece's myDATA leg) have a Tier D component.

---

## 5. Tier D — clearance platforms (separate apps, not this one)

| Country | Platform | Why it is not a profile here |
| --- | --- | --- |
| **Saudi Arabia** | ZATCA (Fatoora) | Direct integration with ZATCA, XAdES signing, QR, no provider layer. ERPGulf's `zatca_erpgulf` |
| **Malaysia (direct)** | MyInvois | Direct LHDN API, clearance before validity. ERPGulf's Malaysia app; the Peppol leg is Tier A above |
| **Italy** | SDI | Central exchange system, FatturaPA XML, no provider choice (intermediaries exist but SDI is the only endpoint) |
| **Poland** | KSeF (2026) | Central platform, FA(2)/FA(3) XML |
| **Hungary** | NAV Online Számla | Real-time reporting of invoice data, not document exchange |
| **Romania** | RO e-Factura | Central platform via SPV, mandatory B2B since 2024 |
| **Spain** | Veri*factu / SII; B2B e-invoicing law pending | Certified software reporting to AEAT; the B2B network is still being defined |
| **Turkey** | e-Fatura (GİB) | Central platform via special integrators |
| **India** | IRP (e-invoice) | JSON schema to the Invoice Registration Portal, IRN + QR |
| **Mexico, Brazil, Chile, Colombia, Peru, Argentina** | CFDI, NF-e, DTE, … | Clearance with government-authorised certifiers; national XML schemas |
| **Egypt** | ETA e-invoicing | Central platform, signed JSON |
| **Serbia** | SEF | Central platform, UBL — the one Tier D that is UBL-based; still single endpoint |

These share the UBL-or-XML and signing know-how but not the provider model. They stay separate apps; nothing in `einvoice_erpgulf` prevents a later `standards/clearance` layer, but it is not planned.

---

## 6. How to judge a new country in one sitting

Answer these in order; the first "no" tells you the tier.

1. **Does the taxpayer choose from several providers?** No → Tier D, separate app. Yes → continue.
2. **Is the document a Peppol PINT or an EN 16931 UBL?** PINT → `PintProfile`; EN 16931 / BIS 3.0 → `EN16931Profile`; CII or Factur-X → EN 16931 profile plus the CII writer (Tier C); national XML → Tier D.
3. **Is there a government-accredited provider list?** Yes → the profile's `provider_label` is "Accredited Service Provider" and the official list may ship in the country fixture (as the FTA list does). No → "Peppol Access Point", dropdown shows only registered adapters.
4. **Is there a fifth corner?** Yes → statuses reach `reported`; the ribbon says "<Authority> Reported". No → statuses end at `delivered`; set `final_reporting_statuses` accordingly.
5. **What is the identifier?** Peppol scheme (ICD) plus the rule from the national tax ID to the endpoint ID → `endpoint_scheme`, `endpoint_id_from_tax_id`.
6. **Which tax categories, which rates, which exemption codes?** → `tax_codes`, `tax_category_labels`, and the VAT Category options in the country fixture.
7. **Regions required on addresses?** → `region_field`, `region_codes`, `region_required`.
8. **Currency decimals?** 2 for most, 3 for OMR/BHD/KWD, 0 for JPY → `currency_decimals`.
9. **Any national rule the ERP must check before sending?** → `validate_document`.
10. **Do the providers take JSON or UBL?** JSON → adapter builds the provider's shape from `self.profile`; UBL → adapter calls `self.build_ubl(doc)`.

Ten answers are the whole profile. Write them into the docstring at the top of `countries/<cc>/profile.py` before the code, with "(verify)" on anything not read from the authority's own specification, and keep them there: the next developer in that country will thank you.

---

## 7. Order of work recommended

1. **Oman** — confirm the four constants, register adapters, sandbox test. Smallest port, nearest customers, proves multi-country on a live site.
2. **Singapore** — complete profile from public specs; wait for an access-point partner for adapters.
3. **Belgium** — first access-point adapter, UBL path end to end, inbound UBL reader. Proves the European route.
4. **Netherlands / Nordics** — each a short profile once Belgium runs.
5. **Germany** — XRechnung extension in the UBL builder, file-export adapter, then CII/ZUGFeRD.
6. **France** — only with a customer, as a project: lifecycle, e-reporting, Factur-X.

Related: [README](../README.md) · [Developer Guide](DEVELOPER_GUIDE.md) · [Testing Guide](TESTING_GUIDE.md) · [End User Guide](USER_GUIDE.md)