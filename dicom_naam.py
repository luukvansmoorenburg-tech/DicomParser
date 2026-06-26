"""
DICOM sequentie-naam generator
================================

Bouwt een leesbare, universele naam voor een DICOM-sequentie uit 4 onderdelen:

    1. Undersampling techniek   -> direct uit tag
    2. Acquisitie weging        -> AFGELEID via if-statements (TR/TE/TI)
    3. Acquisitie tijd          -> direct uit tag
    4. Slice thickness          -> direct uit tag

Resultaat (underscore-formaat), bijv.:   PI_T2_2m23s_4.0mm

Gebruik:
    python dicom_naam.py pad/naar/bestand.dcm      # 1 bestand
    python dicom_naam.py pad/naar/map/             # hele map (recursief)
    python dicom_naam.py map/ --csv namen.csv      # map + resultaat naar CSV
    python dicom_naam.py map/ --per-bestand        # elke slice apart i.p.v. per serie
    python dicom_naam.py map/ --output output/     # kopie per serie -> output/<nieuwe_naam>/
                                                   #   met tag (0018,1030) ProtocolName = nieuwe naam

Geen pad opgegeven? Dan gebruikt het script een meegeleverd
pydicom-testbestand (MR_small.dcm), zodat je het meteen kunt proberen.

Mappen met Philips-data (bv. een MR7700 export) bevatten naast de echte
MR-beelden ook ruis: een DICOMDIR-index, ExamCards en GrayScale Presentation
States (PS_*). Die hebben geen MR-parameters. Het script herkent en negeert
deze automatisch, en geeft standaard EEN naam per serie i.p.v. per slice.
"""

import csv
import os
import re
import sys
import pydicom


# ---------------------------------------------------------------------------
# 1. ONDERDELEN DIE DIRECT UIT TAGS KOMEN
# ---------------------------------------------------------------------------
# Pas hier de tags aan als jouw vendor (Philips/Siemens/GE) andere tags gebruikt.

def onderdeel_undersampling(ds):
    """Undersampling techniek, afgekort uit de Philips-tag (2005,1710).

    Mapping (op trefwoord, dus underscores/spaties maken niet uit):
        bevat 'AI'    -> PI   (bv. 'CS_SENSE_AI' / 'CSSENSE AI')
        bevat 'CS'    -> CS   (bv. 'CSSENSE')
        bevat 'SENSE' -> s    (bv. 'SENSE')

    Tag (2005,1710) kan genest zitten, dus we zoeken op elk niveau. Valt terug
    op de standaardtag (0018,9078) als (2005,1710) ontbreekt.
    """
    techniek = zoek_tag(ds, (0x2005, 0x1710),
                        "ParallelAcquisitionTechnique", (0x0018, 0x9078))
    if not techniek:
        return "noPI"   # geen parallel imaging gevonden

    t = str(techniek).upper()
    if "AI" in t:
        return "PI"
    if "CS" in t:
        return "CS"
    if "SENSE" in t:
        return "s"
    return str(techniek)   # onbekende techniek -> ruwe waarde behouden


def onderdeel_acquisitietijd(ds):
    """Acquisitie(scan)tijd -> scanduur in MINUTEN.

    De scanduur staat in (0018,9073) AcquisitionDuration, in SECONDEN.
    We rekenen om naar minuten (bv. 143.6 s -> '2.4min'). Bij Philips kan de
    tag genest zitten, dus we zoeken op elk niveau. We proberen ook de door
    de gebruiker genoemde private tag (2005,1033) als fallback.
    """
    seconden = _getal(zoek_tag(ds, "AcquisitionDuration",
                               (0x0018, 0x9073), (0x2005, 0x1033)))
    if seconden is None:
        return "geenTijd"
    # Omrekenen naar minuten + seconden, bv. 143 s -> '2m23s'.
    totaal = round(seconden)
    minuten, sec = divmod(totaal, 60)
    return f"{minuten}m{sec:02d}s"


def onderdeel_slicethickness(ds):
    """Slice thickness -> uit SliceThickness (0018,0050), in mm."""
    dikte = ds.get("SliceThickness", None)
    if dikte is None:
        return "geenDikte"
    return f"{float(dikte):.1f}mm"


# ---------------------------------------------------------------------------
# 2. ACQUISITIE WEGING  -> hier zitten de if-statements die je zelf uitbreidt
# ---------------------------------------------------------------------------
# Logica op basis van Repetition Time (TR), Echo Time (TE) en Inversion Time (TI).
# Drempelwaarden zijn vuistregels voor 1.5T/3T MR; pas ze aan naar wens.

# Drempels (in milliseconden) -- bovenin zodat je ze makkelijk tunet
TR_KORT = 800        # < 800 ms  = "korte TR"
TR_LANG = 2000       # > 2000 ms = "lange TR"
TE_KORT = 30         # < 30 ms   = "korte TE"
TE_LANG = 80         # > 80 ms   = "lange TE"


def onderdeel_weging(ds):
    """
    Bepaalt de acquisitie weging via if-statements.

    Voeg hier gerust extra elif-takken toe (DWI, MRA, T2*, etc.).
    """
    tr = _getal(ds.get("RepetitionTime", None))     # (0018,0080)
    te = _getal(ds.get("EchoTime", None))           # (0018,0081)
    ti = _getal(ds.get("InversionTime", None))      # (0018,0082)
    scan_seq = str(ds.get("ScanningSequence", ""))  # (0018,0020) bv. 'IR', 'EP'

    # --- Inversion Recovery sequenties (TI aanwezig) -------------------------
    if ti is not None and ti > 0:
        if ti < 500:
            return "STIR"           # korte TI -> vet onderdrukking
        if ti >= 1500:
            return "FLAIR"          # lange TI -> vocht onderdrukking
        return "IR"                 # overige inversion recovery

    # --- Geen geldige TR/TE -> kunnen we niets over zeggen -------------------
    if tr is None or te is None:
        return "onbekend"

    # De echo-tijd (TE) is de betrouwbaarste discriminator, dus die leidt.
    # De repetitie-tijd (TR) bevestigt alleen. Reden: een T2 TSE/FSE-opname
    # kan een vrij korte TR hebben (~1500 ms) maar nog steeds een lange TE
    # (~140 ms) -> dat is gewoon T2. Eisen we ook TR > 2000, dan vallen die
    # ten onrechte in 'mixed'.

    # --- T2*: gradient echo (korte TE) --------------------------------------
    # Apart afvangen vóór de T1/T2-regels, want GR-echo gedraagt zich anders.
    if "GR" in scan_seq and te < TE_LANG:
        return "T2ster"

    # --- T2 weging: lange TE ------------------------------------------------
    if te > TE_LANG:
        return "T2"

    # --- Korte TE -> T1 of PD, afhankelijk van TR ---------------------------
    if te < TE_KORT:
        if tr < TR_KORT:
            return "T1"             # korte TR + korte TE
        if tr > TR_LANG:
            return "PD"             # lange TR + korte TE
        # tussenliggende TR met korte TE -> meestal nog steeds T1-achtig
        return "T1"

    # --- Middenlange TE (30-80 ms) ------------------------------------------
    # Korte TR -> T1-gewogen; lange TR -> richting PD; anders niet eenduidig.
    if tr < TR_KORT:
        return "T1"
    if tr > TR_LANG:
        return "PD"
    return "mixed"


# Wegingen waarbij het script er NIET zeker van is. Komt zo'n weging voor
# (bv. bij een DWI, MRA of andere techniek die de TR/TE-regels niet vangen),
# dan vragen we de naam handmatig via een popup.
ONZEKERE_WEGINGEN = {"mixed", "onbekend"}


def naam_is_onzeker(resultaat):
    """True als de gegenereerde naam onbetrouwbaar is en handmatig moet."""
    return resultaat.get("weging") in ONZEKERE_WEGINGEN


# ---------------------------------------------------------------------------
# HULPFUNCTIES
# ---------------------------------------------------------------------------

def _getal(waarde):
    """Maak een float van een DICOM-waarde, of None als het niet kan."""
    if waarde is None:
        return None
    try:
        return float(waarde)
    except (TypeError, ValueError):
        return None


def zoek_tag(ds, *tags):
    """Zoek een tag op ELK niveau (ook genest in sequences) en geef de waarde.

    Philips-exports stoppen tags als (0018,9078) en (0018,9073) vaak in
    geneste functional-group sequences i.p.v. op het topniveau. ds.get()
    kijkt alleen bovenaan; deze functie doorzoekt de hele boom.

    'tags' zijn keywords ('AcquisitionDuration') of (group, element)-tuples.
    De eerste tag die ergens een waarde heeft, wint. None als niets gevonden.
    """
    # Eerst snel op topniveau proberen (goedkoper dan de hele boom doorlopen).
    for t in tags:
        val = ds.get(t)
        if val not in (None, ""):
            return val

    # Daarna recursief door alle sequences heen.
    def loop(dataset):
        for el in dataset:
            if el.VR == "SQ":
                for item in el.value:
                    gevonden = loop(item)
                    if gevonden is not None:
                        return gevonden
            else:
                for t in tags:
                    # match op keyword OF op (group, element)-tuple
                    if isinstance(t, tuple):
                        if (el.tag.group, el.tag.element) == t and el.value not in (None, ""):
                            return el.value
                    elif el.keyword == t and el.value not in (None, ""):
                        return el.value
        return None

    return loop(ds)


def _nummer(waarde):
    """Toon een getal netjes: 2.0 -> '2', 2.5 -> '2.5'."""
    try:
        f = float(waarde)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(waarde)


# ---------------------------------------------------------------------------
# SAMENSTELLEN VAN DE NAAM
# ---------------------------------------------------------------------------

SCHEIDINGSTEKEN = "_"   # underscore-formaat


def maak_naam(ds):
    delen = [
        onderdeel_undersampling(ds),
        onderdeel_weging(ds),
        onderdeel_acquisitietijd(ds),
        onderdeel_slicethickness(ds),
    ]
    return SCHEIDINGSTEKEN.join(delen)


# SOP Class UID van een gewoon MR-beeld ("MR Image Storage").
# Alleen deze bestanden bevatten de TR/TE/SliceThickness die we nodig hebben.
# Presentation States, DICOMDIR en Philips-eigen raw-objecten hebben een
# andere SOP Class en worden zo netjes overgeslagen.
MR_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.4"


def is_mr_beeld(ds):
    """True als dit een echt MR-beeld is (en geen PS / raw / DICOMDIR)."""
    if str(ds.get("Modality", "")) != "MR":
        return False
    if str(ds.get("SOPClassUID", "")) != MR_IMAGE_STORAGE:
        return False
    # Zonder TR en TE kunnen we toch geen weging bepalen -> als ruis behandelen.
    return ds.get("RepetitionTime") is not None and ds.get("EchoTime") is not None


def verwerk_bestand(pad):
    """Lees 1 DICOM-bestand en geef een dict met de onderdelen + naam terug.

    Retourneert None als het bestand geen leesbaar MR-beeld is (ruis-bestanden
    zoals DICOMDIR, ExamCards en Presentation States worden overgeslagen).
    """
    try:
        # force=True zodat ook DICOM-bestanden zonder .dcm-extensie (IM_0001,
        # zoals in Philips-exports) gelezen worden. stop_before_pixels=True
        # scheelt veel tijd: we hebben alleen de header-tags nodig.
        ds = pydicom.dcmread(pad, force=True, stop_before_pixels=True)
    except Exception:
        return None   # geen (geldig) DICOM-bestand -> overslaan

    if not is_mr_beeld(ds):
        return None   # ruis (PS / DICOMDIR / raw) -> overslaan

    return {
        "bestand": pad,
        "serie_uid": str(ds.get("SeriesInstanceUID", "")),
        "serienummer": str(ds.get("SeriesNumber", "")),
        "seriebeschrijving": str(ds.get("SeriesDescription", "")),
        # Originele (0018,1030) ProtocolName -> tonen we in de popup als referentie.
        "origineel_protocol": str(ds.get("ProtocolName", "")),
        "undersampling": onderdeel_undersampling(ds),
        "weging": onderdeel_weging(ds),
        "acquisitietijd": onderdeel_acquisitietijd(ds),
        "slicethickness": onderdeel_slicethickness(ds),
        "naam": maak_naam(ds),
    }


def verzamel_bestanden(map_pad):
    """Loop recursief door een map en geef alle bestandspaden terug."""
    paden = []
    for wortel, _dirs, bestanden in os.walk(map_pad):
        for naam in bestanden:
            paden.append(os.path.join(wortel, naam))
    return sorted(paden)


def groepeer_per_serie(resultaten):
    """Vat losse bestand-resultaten samen tot EEN resultaat per serie.

    Alle slices van dezelfde serie hebben dezelfde TR/TE/dikte en dus
    dezelfde gegenereerde naam. We pakken het eerste bestand per serie en
    voegen het aantal slices toe. Series blijven in de volgorde waarin ze
    voor het eerst voorkomen.
    """
    series = {}
    for r in resultaten:
        uid = r.get("serie_uid") or r["bestand"]
        if uid not in series:
            eerste = dict(r)
            eerste["aantal_slices"] = 1
            series[uid] = eerste
        else:
            series[uid]["aantal_slices"] += 1
    return list(series.values())


def print_resultaat(r):
    """Print het overzicht voor 1 bestand."""
    print(f"Bestand          : {r['bestand']}")
    print(f"  1. Undersampling : {r['undersampling']}")
    print(f"  2. Weging        : {r['weging']}")
    print(f"  3. Acquisitietijd: {r['acquisitietijd']}")
    print(f"  4. Slicethickness: {r['slicethickness']}")
    print(f"  => Naam          : {r['naam']}")
    print()


def print_serie(r):
    """Print het overzicht voor 1 serie (compacter dan per bestand)."""
    nr = r.get("serienummer", "?")
    desc = r.get("seriebeschrijving", "")
    n = r.get("aantal_slices", 1)
    print(f"Serie {nr:>6}  ({n} slices)  {desc}")
    print(f"  1. Undersampling : {r['undersampling']}")
    print(f"  2. Weging        : {r['weging']}")
    print(f"  3. Acquisitietijd: {r['acquisitietijd']}")
    print(f"  4. Slicethickness: {r['slicethickness']}")
    print(f"  => Naam          : {r['naam']}")
    print()


def schrijf_csv(resultaten, csv_pad):
    """Schrijf alle resultaten naar een CSV die NL-Excel netjes in kolommen opent.

    - encoding 'utf-8-sig' = UTF-8 met BOM -> Excel herkent de tekens goed
    - de regel 'sep=,' bovenaan dwingt (Engelse) Excel de komma als scheiding
      te gebruiken, ongeacht de Windows-regio-instellingen.
    """
    velden = ["serienummer", "seriebeschrijving", "aantal_slices",
              "undersampling", "weging", "acquisitietijd",
              "slicethickness", "naam", "bestand"]
    # Nette, leesbare kopteksten in plaats van de interne veldnamen
    kopteksten = {
        "serienummer": "Serie",
        "seriebeschrijving": "Seriebeschrijving",
        "aantal_slices": "Aantal slices",
        "undersampling": "Undersampling",
        "weging": "Weging",
        "acquisitietijd": "Acquisitietijd",
        "slicethickness": "Slicethickness",
        "naam": "Gegenereerde naam",
        "bestand": "Voorbeeldbestand",
    }
    with open(csv_pad, "w", newline="", encoding="utf-8-sig") as f:
        f.write("sep=,\n")   # vertelt Excel: gebruik komma als scheidingsteken
        # extrasaction='ignore' -> velden die in een rij ontbreken (bv.
        # serie_uid) worden genegeerd i.p.v. een fout te geven.
        schrijver = csv.DictWriter(f, fieldnames=velden, delimiter=",",
                                   extrasaction="ignore")
        schrijver.writerow(kopteksten)
        for r in resultaten:
            schrijver.writerow(r)
    print(f"CSV weggeschreven naar: {csv_pad}  ({len(resultaten)} regels)")


# ---------------------------------------------------------------------------
# POPUP VOOR HANDMATIGE NAAM (bij onbekende techniek, bv. DWI)
# ---------------------------------------------------------------------------

def vraag_naam_popup(resultaat):
    """Toon een tkinter-popup om de serienaam handmatig in te vullen.

    De popup toont de originele ProtocolName-tag + serie-info als referentie,
    en een bewerkbaar veld met de automatisch voorgestelde naam. Geeft de
    ingevulde naam terug, of None als de gebruiker annuleert / het venster sluit.
    """
    # tkinter pas hier importeren: het script blijft zo bruikbaar op systemen
    # zonder grafische omgeving zolang er geen popup nodig is.
    import tkinter as tk
    from tkinter import ttk

    keuze = {"naam": None}

    venster = tk.Tk()
    venster.title("Naam handmatig invullen")
    venster.attributes("-topmost", True)   # vóór andere vensters
    venster.resizable(False, False)

    rij = ttk.Frame(venster, padding=16)
    rij.grid(sticky="nsew")

    ttk.Label(
        rij,
        text="Deze serie kon niet automatisch herkend worden\n"
             "(onbekende acquisitietechniek, bv. DWI).",
        font=("Segoe UI", 10, "bold"),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

    # Referentie-info -- vooral de originele ProtocolName-tag.
    info = [
        ("Originele ProtocolName (0018,1030):", resultaat.get("origineel_protocol") or "(leeg)"),
        ("Serienummer:", resultaat.get("serienummer", "?")),
        ("Seriebeschrijving:", resultaat.get("seriebeschrijving") or "(leeg)"),
        ("Automatisch voorstel:", resultaat.get("naam", "")),
    ]
    for i, (label, waarde) in enumerate(info, start=1):
        ttk.Label(rij, text=label, font=("Segoe UI", 9, "bold")).grid(
            row=i, column=0, sticky="w", padx=(0, 10), pady=2)
        ttk.Label(rij, text=waarde, font=("Consolas", 9)).grid(
            row=i, column=1, sticky="w", pady=2)

    ttk.Separator(rij, orient="horizontal").grid(
        row=len(info) + 1, column=0, columnspan=2, sticky="ew", pady=10)

    ttk.Label(rij, text="Nieuwe naam:", font=("Segoe UI", 9, "bold")).grid(
        row=len(info) + 2, column=0, sticky="w", padx=(0, 10))

    invoer = ttk.Entry(rij, width=40, font=("Consolas", 10))
    invoer.insert(0, resultaat.get("naam", ""))   # voorstel als startwaarde
    invoer.grid(row=len(info) + 2, column=1, sticky="ew")
    invoer.focus_set()
    invoer.select_range(0, tk.END)

    def bevestig():
        tekst = invoer.get().strip()
        if tekst:
            keuze["naam"] = tekst
            venster.destroy()

    def annuleer():
        keuze["naam"] = None
        venster.destroy()

    knoppen = ttk.Frame(rij)
    knoppen.grid(row=len(info) + 3, column=0, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(knoppen, text="Overslaan", command=annuleer).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(knoppen, text="Opslaan", command=bevestig).grid(row=0, column=1)

    venster.bind("<Return>", lambda _e: bevestig())
    venster.bind("<Escape>", lambda _e: annuleer())

    venster.mainloop()
    return keuze["naam"]


# ---------------------------------------------------------------------------
# WEGSCHRIJVEN NAAR OUTPUT-MAP (gehernoemde kopieën met aangepaste tag)
# ---------------------------------------------------------------------------

def _veilige_mapnaam(naam):
    """Maak een naam veilig als map-/bestandsnaam onder Windows.

    Vervangt tekens die niet in een Windows-pad mogen ( \\ / : * ? " < > | )
    door een underscore. Onze namen ('noPI_T2_1041_2.5mm') zijn meestal al
    veilig; de punt in '2.5mm' mag gewoon blijven staan.
    """
    return re.sub(r'[\\/:*?"<>|]', "_", naam).strip()


def schrijf_naar_output(resultaten_per_bestand, output_map):
    """Kopieer alle MR-slices naar een output-map, per serie in een submap.

    - Elke serie krijgt een submap genoemd naar de gegenereerde naam.
    - Bij dubbele namen wordt _2, _3, ... toegevoegd zodat geen serie elkaar
      overschrijft.
    - In ELKE slice wordt tag (0018,1030) ProtocolName overschreven met de
      nieuwe naam. De bestandsnaam wordt: <nieuwe_naam>_<volgnr>.dcm
    - Pixeldata blijft behouden (we lezen hier het volledige bestand).

    'resultaten_per_bestand' = lijst van per-bestand dicts (NIET gegroepeerd),
    want we hebben elk los slice-pad nodig om te kopiëren.
    """
    # Groepeer de losse bestanden op serie, met behoud van volgorde.
    series = {}
    for r in resultaten_per_bestand:
        uid = r.get("serie_uid") or r["bestand"]
        series.setdefault(uid, []).append(r)

    os.makedirs(output_map, exist_ok=True)

    gebruikte_namen = {}     # basisnaam -> hoe vaak al gebruikt (voor _2, _3)
    totaal_bestanden = 0

    overgeslagen = 0

    for uid, slices in series.items():
        eerste = slices[0]

        # Onzekere weging (bv. DWI) -> popup om de naam handmatig in te vullen.
        if naam_is_onzeker(eerste):
            print(f"  ? Serie {eerste.get('serienummer','?')} "
                  f"('{eerste.get('seriebeschrijving','')}') niet automatisch "
                  f"herkend -> popup geopend...")
            handmatig = vraag_naam_popup(eerste)
            if not handmatig:
                print(f"    -> overgeslagen (geen naam ingevuld).")
                overgeslagen += 1
                continue
            basis = _veilige_mapnaam(handmatig)
        else:
            basis = _veilige_mapnaam(eerste["naam"])

        # Uniek maken bij botsing: _2, _3, ...
        n = gebruikte_namen.get(basis, 0) + 1
        gebruikte_namen[basis] = n
        serie_naam = basis if n == 1 else f"{basis}_{n}"

        serie_map = os.path.join(output_map, serie_naam)
        os.makedirs(serie_map, exist_ok=True)

        for i, r in enumerate(slices, start=1):
            try:
                # Volledig bestand lezen (mét pixeldata) zodat de kopie compleet is.
                ds = pydicom.dcmread(r["bestand"], force=True)
            except Exception as e:
                print(f"  ! Kon {r['bestand']} niet lezen: {e}")
                continue

            # ---- DE KERN: tag (0018,1030) ProtocolName overschrijven --------
            ds.ProtocolName = serie_naam
            # Voor de zekerheid ook SeriesDescription meenemen, zodat viewers
            # die dát tonen ook de nieuwe naam laten zien. (Optioneel.)
            # ds.SeriesDescription = serie_naam

            doel = os.path.join(serie_map, f"{serie_naam}_{i:04d}.dcm")
            ds.save_as(doel)
            totaal_bestanden += 1

        print(f"  Serie -> {serie_naam}  ({len(slices)} slices)")

    geschreven_series = len(series) - overgeslagen
    print(f"\nOutput klaar: {geschreven_series} serie(s), {totaal_bestanden} "
          f"bestand(en) geschreven naar: {output_map}")
    if overgeslagen:
        print(f"  ({overgeslagen} serie(s) overgeslagen: geen handmatige naam ingevuld.)")


def main():
    args = sys.argv[1:]

    # Optioneel: --per-bestand om elke slice apart te tonen i.p.v. per serie
    per_bestand = False
    if "--per-bestand" in args:
        per_bestand = True
        args.remove("--per-bestand")

    # Optioneel: --csv <pad> om het resultaat ook naar CSV te schrijven
    csv_pad = None
    if "--csv" in args:
        i = args.index("--csv")
        try:
            csv_pad = args[i + 1]
        except IndexError:
            print("Fout: --csv verwacht een bestandsnaam erachter.")
            return
        del args[i:i + 2]

    # Optioneel: --output <map> om gehernoemde kopieën weg te schrijven,
    # met tag (0018,1030) ProtocolName overschreven door de nieuwe naam.
    output_map = None
    if "--output" in args:
        i = args.index("--output")
        try:
            output_map = args[i + 1]
        except IndexError:
            print("Fout: --output verwacht een mapnaam erachter.")
            return
        del args[i:i + 2]

    # Bepaal het pad (bestand of map)
    if args:
        pad = args[0]
    else:
        from pydicom.data import get_testdata_file
        pad = get_testdata_file("MR_small.dcm")
        print(f"(Geen pad opgegeven -- gebruik testbestand: {pad})\n")

    # --- Map of enkel bestand? ----------------------------------------------
    if os.path.isdir(pad):
        bestanden = verzamel_bestanden(pad)
        print(f"Map gevonden: {pad}  ({len(bestanden)} bestanden om te proberen)\n")
        resultaten = []
        for b in bestanden:
            r = verwerk_bestand(b)
            if r is not None:          # alleen echte MR-beelden
                resultaten.append(r)

        if per_bestand:
            # Elke slice apart tonen.
            for r in resultaten:
                print_resultaat(r)
            uitvoer = resultaten
            print(f"Klaar: {len(resultaten)} MR-beeld(en) verwerkt "
                  f"van {len(bestanden)} bestand(en) in de map.")
        else:
            # Standaard: groeperen tot EEN naam per serie.
            uitvoer = groepeer_per_serie(resultaten)
            for r in uitvoer:
                print_serie(r)
            print(f"Klaar: {len(uitvoer)} serie(s) gevonden "
                  f"({len(resultaten)} MR-beelden van {len(bestanden)} "
                  f"bestand(en) in de map; ruis automatisch overgeslagen).")

        if csv_pad and uitvoer:
            schrijf_csv(uitvoer, csv_pad)

        # Gehernoemde kopieën wegschrijven (gebruikt ALLE losse slices).
        if output_map and resultaten:
            print(f"\nKopiëren naar output-map (ProtocolName -> nieuwe naam):")
            schrijf_naar_output(resultaten, output_map)

    else:
        r = verwerk_bestand(pad)
        if r is None:
            print(f"Kon geen geldig DICOM lezen uit: {pad}")
            return
        print_resultaat(r)
        if csv_pad:
            schrijf_csv([r], csv_pad)


if __name__ == "__main__":
    main()
