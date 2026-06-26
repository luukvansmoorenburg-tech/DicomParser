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

    MotionFree (CS-SENSE MultiVANE) krijgt altijd 'MF' als prefix,
    ongeacht de onderliggende techniek.

    Overige mapping (op trefwoord):
        bevat 'AI'    -> PI   (bv. 'CS_SENSE_AI' / 'CSSENSE AI')
        bevat 'CS'    -> CS   (bv. 'CSSENSE')
        bevat 'SENSE' -> s    (bv. 'SENSE')
    """
    techniek = zoek_tag(ds, (0x2005, 0x1710),
                        "ParallelAcquisitionTechnique", (0x0018, 0x9078))

    t = str(techniek).upper().replace(" ", "").replace("_", "") if techniek else ""

    # MotionFree = CS-SENSE + MultiVANE/Propeller trajectory
    # Detecteer CS eerst, dan controleer of het een MultiVANE-sequentie is
    is_cs = "CS" in t
    if is_cs:
        for naam_tag in ("ProtocolName", "SeriesDescription"):
            naam = str(ds.get(naam_tag, "")).upper().replace(" ", "").replace("-", "").replace("_", "")
            if any(k in naam for k in ("MOTIONFREE", "MULTIVANE", "PROPELLER")):
                return "MF"
            # SeriesDescription begint met 'MF' + CS = MotionFree
            if naam.startswith("MF") and len(naam) > 2 and naam[2].isalpha():
                return "MF"

    if not techniek or t in ("NONE", ""):
        return "noPI"

    if "AI" in t:
        return "AI"
    if "SMARTSPEEDPREC" in t or "SMARTSPEED" in t:
        return "AI"
    if "CS" in t:
        return "CS"
    if "SENSE" in t:
        return "S"
    return str(techniek)   # unknowne techniek -> ruwe waarde behouden


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
        return "noTime"
    # Omrekenen naar minuten + seconden, bv. 143 s -> '2m23s'.
    totaal = round(seconden)
    minuten, sec = divmod(totaal, 60)
    if minuten == 0:
        return f"{sec}s"
    return f"{minuten}m{sec:02d}s"


def onderdeel_slicethickness(ds):
    """Slice thickness -> uit SliceThickness (0018,0050), in mm."""
    dikte = zoek_tag(ds, "SliceThickness", (0x0018, 0x0050))
    if dikte is None:
        return "noThick"
    return f"{_nummer(dikte)}mm"


# ---------------------------------------------------------------------------
# 2. ACQUISITIE WEGING  -> hier zitten de if-statements die je zelf uitbreidt
# ---------------------------------------------------------------------------
# Logica op basis van ProtocolName, ScanningSequence, TR/TE/TI, b-waarde en
# acquisitie-type.  Drempelwaarden zijn vuistregels voor 1.5T/3T MR.

# Drempels (in milliseconden) -- bovenin zodat je ze makkelijk tunet

# Spin Echo / TSE drempels
TR_KORT  = 800       # < 800 ms  = "korte TR"
TR_LANG  = 1500      # > 1500 ms = "lange TR"
TE_KORT  = 30        # < 30 ms   = "korte TE"   (SE/TSE -> T1)
TE_LANG  = 55        # > 55 ms   = "lange TE"  (SE/TSE -> T2)
                     # 30-55 ms + lange TR -> PD
                     # T2 begint al bij 55 ms omdat fat-sat T2 kortere TE
                     # kan gebruiken (fatsat-puls behoudt T2-weging ook bij
                     # kortere TE, geeft meer SNR)

# Gradient Echo (FFE) drempels -- GRE gebruikt veel kortere TEs dan SE
GRE_TE_T1      = 10  # TE < 10 ms   = T1FFE kandidaat
GRE_TE_T2      = 15  # TE > 15 ms   = T2FFE kandidaat
GRE_FA_T2      = 25  # flip angle < 25° = T2FFE (T2FFE ~20°, mFFE ~25°+)
                     # mFFE wordt eerder gevangen via ETL > 1, dus geen overlap
                     # T1FFE: geen flip angle drempel, TE < 10ms is voldoende

# Inversion Recovery drempels
TI_STIR  = 500       # TI < 500 ms  = STIR  (vet-onderdrukking)
TI_FLAIR = 1500      # TI >= 1500 ms = FLAIR (vocht-onderdrukking)

# ---------------------------------------------------------------------------
# PROTOCOL-NAAM LOOKUP
# ---------------------------------------------------------------------------
# Controleer de ProtocolName-tag EERST op bekende sleutelwoorden.
# Volgorde is van meest-specifiek naar minst-specifiek binnen elke groep,
# zodat bv. "DWIBS" eerder matcht dan "DWI".
# Geeft (weging, is_3d_override) terug, of None als niets matcht.
# is_3d_override=True forceert een 3D-prefix los van de MRAcquisitionType-tag.

PROTOCOL_SLEUTELWOORDEN = [
    # -------------------------------------------------------------------------
    # Regels: meest-specifiek EERST binnen elke groep.
    # Spaties, koppeltekens en underscores worden vóór vergelijking verwijderd,
    # dus "TSE-DWI", "TSE_DWI" en "TSEDWI" raken allemaal de "TSEDWI"-regel.
    # Kolommen: (sleutelwoord, uitvoer-weging, forceer_3d_prefix)
    # forceer_3d=True: altijd "3D" voor de weging, ook als de tag het niet zegt.
    # -------------------------------------------------------------------------

    # --- Diffusie ------------------------------------------------------------
    ("IVIM",         "IVIM",          False),   # protocol naam only; anders DWI
    ("DWIBS",        "DWIBS",         False),   # whole-body DWI met STIR
    ("DTI",          "DTI",           False),   # diffusion tensor (richtingen via tag)
    ("IRIS",         "IRIS-DWI",      False),   # Philips multi-shot productnaam
    ("MULTISHOT",    "IRIS-DWI",      False),
    ("TSEDWI",       "TSEDWI",        False),   # TSE readout DWI
    ("DWITSE",       "TSEDWI",        False),
    ("ADC",          "ADC",           False),   # berekende ADC-kaart
    ("DWI",          "DWI",           False),   # standaard single-shot EPI DWI

    # --- EPI subtypes --------------------------------------------------------
    ("TFEEP",        "TFE-EPI",       False),   # Philips turbo field echo EPI
    ("TFEEPI",       "TFE-EPI",       False),
    ("TFE-EPI",      "TFE-EPI",       False),
    ("FFEEP",        "FFE-EPI",       False),   # Philips fast field echo EPI
    ("FFEEPI",       "FFE-EPI",       False),
    ("FFE-EPI",      "FFE-EPI",       False),
    ("FEEPI",        "FFE-EPI",       False),   # Philips field echo EPI (FE_EPI)
    ("FE-EPI",       "FFE-EPI",       False),
    ("FEEP",         "FFE-EPI",       False),
    ("SEEP",         "SE-EPI",        False),   # spin echo EPI
    ("SEEPI",        "SE-EPI",        False),
    ("SE-EPI",       "SE-EPI",        False),
    ("FMRI",         "fMRI",          False),   # functional MRI
    ("BOLD",         "fMRI",          False),   # BOLD fMRI

    # --- Perfusie / vasculair ------------------------------------------------
    # DCE/DSC: afgehandeld in STAP 0 (custom namen)
    # DSC: afgehandeld in STAP 0 (custom naam T2*DSC)
    # PWI/ASL: afgehandeld in STAP 0 (custom namen T2*DSC / ASL / 3dASL)
    # QFLOW (Phase Contrast flow quantification) -> naam behouden
    ("QFLOW",        "QFLOW",         False),

    # TOF/MRA/PCA: afgehandeld in STAP 0 (2D/3D onderscheid)

    # --- Susceptibility ------------------------------------------------------
    # SWI/QSM: afgehandeld in STAP 0

    # --- Spectroscopie -------------------------------------------------------
    # MRS: afgehandeld in STAP 0 (SVS/CSI/MRS + techniek-suffix)

    # --- Inversion Recovery --------------------------------------------------
    ("PSIR",         "PSIR",          False),   # phase sensitive IR
    ("DIR",          "DIR",           False),   # double inversion recovery
    ("3DT1FLAIR",    "T1FLAIR",       True),
    ("T1FLAIR",      "T1FLAIR",       False),
    ("FLAIRSSH",     "FLAIR-SSh",     False),   # Single Shot FLAIR
    ("FLAIRHASTE",   "FLAIR-SSh",     False),
    ("HASTEFLAIR",   "FLAIR-SSh",     False),
    ("SSHFLAIR",     "FLAIR-SSh",     False),
    ("3DFLAIR",      "FLAIR",         True),
    ("FLAIR",        "FLAIR",         False),
    ("3DSTIR",       "STIR",          True),
    ("STIR",         "STIR",          False),
    ("IRTFE",        "T1TFE",         True),    # IR-prepped TFE (voor IR!)
    ("IR",           "IR",            False),   # overige inversion recovery

    # --- Dixon -------------------------------------------------------------------
    ("MDIXON",       "mDixon",        False),   # Philips mDixon fat/water separatie
    ("DIXON",        "mDixon",        False),

    # --- Gradient Echo (Philips FFE-naamgeving) ------------------------------
    ("BFFE",         "bFFE",          False),   # balanced FFE (SSFP)
    ("SSFP",         "bFFE",          False),
    ("FIESTA",       "bFFE",          False),   # GE-naam voor SSFP
    ("TRUFI",        "bFFE",          False),   # Siemens-naam voor SSFP
    ("MFFE",         "mFFE",          False),   # multi-echo FFE
    ("T2FFE",        "T2FFE",         False),   # T2* gradient echo
    ("T2WFFE",       "T2FFE",         False),   # T2-weighted FFE (bv. AI_T2W_FFE)
    ("T2STAR",       "T2FFE",         False),
    ("T1FFE",        "T1FFE",         False),   # T1 gradient echo
    ("MPRAGE",       "T1TFE",         True),    # Siemens 3D IR-prepped GRE -> 3DT1TFE
    ("IRTFE",        "T1TFE",         True),    # Philips IR-TFE -> 3DT1TFE (voor TFE!)
    ("BTFE",         "bTFE",          False),   # balanced TFE (Cine) -> voor TFE!
    ("TFE",          "TFE",           False),   # turbo field echo (Philips prep-GRE)
    ("VIBE",         "T1FFE",         True),    # Siemens 3D T1 GRE breath-hold
    ("FLASH",        "T1FFE",         False),   # Siemens GRE naam
    ("FISP",         "T2FFE",         False),   # Siemens GRE naam
    ("GRASS",        "T2FFE",         False),   # GE GRE naam
    ("FFE",          "FFE",           False),   # generieke Philips gradient echo

    # --- NerveView -----------------------------------------------------------
    ("NERVEVIEW",    "NerveView",     True),    # Philips 3D black-blood TSE -> 3dNerveView
    ("3DNV",         "NerveView",     True),    # veelgebruikte afkorting
    ("NERVEV",       "NerveView",     True),

    # --- mDixon TSE ----------------------------------------------------------
    ("MDIXONTSE",    "T2-mDix",       False),
    ("DIXONTSE",     "T2-mDix",       False),
    ("TSEDIXON",     "T2-mDix",       False),
    ("TSEMDI",       "T2-mDix",       False),

    # --- UTE -----------------------------------------------------------------
    ("UTE",          "UTE",           False),

    # --- GRaSE ---------------------------------------------------------------
    ("GRASE",        "GRaSE",         False),
    ("GRASE3D",      "GRaSE",         True),

    # --- DRIVE / RESTORE (driven equilibrium TSE) ----------------------------
    ("DRIVE",        "T2Drive",       True),    # Philips -> 3DT2Drive
    ("RESTORE",      "T2Drive",       True),    # Siemens equivalent
    ("FRFSE",        "T2Drive",       True),    # GE equivalent

    # Cine: afgehandeld in STAP 0 (view-suffix detectie)

    # --- MRE (MR Elastography) -----------------------------------------------
    # MRE: afgehandeld in STAP 0 (techniek-suffix)

    # --- 4D Flow -------------------------------------------------------------
    ("4DFLOW",       "4dFlow",        False),
    ("4D_FLOW",      "4dFlow",        False),
    ("4DPCMRA",      "4dFlow",        False),

    # --- Cardiac T1 mapping --------------------------------------------------
    ("SHMOLLI",      "T1map-ShMOLLI", False),   # meest-specifiek eerst
    ("MOLLI",        "T1map-MOLLI",   False),
    ("SASHA",        "T1map-SASHA",   False),

    # --- VANE / MultiVANE ----------------------------------------------------
    # 4D VANE en 3D VANE worden VOOR de protocol-lookup afgehandeld in
    # onderdeel_weging (speciale logica voor mDixon-check en 4dFB).
    ("MOTIONFREE",   "T2",            False),   # CS-SENSE MultiVANE; MF komt uit undersampling
    ("MULTIVANE",    "T2MV",          False),   # SENSE MultiVANE

    # --- Single Shot TSE (HASTE / SS-TSE) -----------------------------------
    ("HASTE",        "T2SSh",           False),
    ("SSTSE",        "T2SSh",           False),
    ("SS-TSE",       "T2SSh",           False),
    ("SSHTSE",       "T2SSh",           False),
    ("T2SSH",        "T2SSh",           False),   # eerder hernoemde bestanden
    ("SSH",          "T2SSh",           False),   # eerder hernoemde bestanden

    # --- MRCP ----------------------------------------------------------------
    ("MRCP",         "T2-MRCP",       False),   # zware T2 TSE (galwegen, urethra, CSF)

    # Mapping: afgehandeld in STAP 0 (techniek-suffix voor T2map/T2starmap)

    # --- Weging (voor SPACE/CUBE zodat T1 SPACE correct als T1 herkend wordt) --
    ("3DT2",         "T2",            True),
    ("3DT1",         "T1",            True),
    ("3DPD",         "PD",            True),
    ("T2",           "T2",            False),
    ("T1",           "T1",            False),
    ("PD",           "PD",            False),

    # --- Spin Echo / TSE / 3D varianten (na T1/T2 zodat T1 SPACE -> T1 wint) --
    ("SPACE",        "T2",            True),    # Siemens 3D TSE
    ("CUBE",         "T2",            True),    # GE 3D TSE
    # VIEW-varianten: afgehandeld in STAP 0 (T1/T2 uit tags, BB-suffix)
]


def protocol_naam_weging(ds):
    """Zoek de ProtocolName op bekende sleutelwoorden (meest-specifiek eerst).

    Korte sleutelwoorden (<= 3 tekens, bv. 'IR', 'PD', 'T1', 'T2') vereisen
    een woordgrens zodat 'IR' niet matcht in 'PIRADS'.
    Geeft (weging_string, forceer_3d) terug, of None als niets matcht.
    """
    raw = str(ds.get("ProtocolName", "")).upper()
    if not raw:
        return None
    # Strip WIP-prefix
    raw_strip = raw.lstrip()
    if raw_strip.startswith("WIP ") or raw_strip.startswith("WIP_"):
        raw = raw_strip[4:]
    # Volledig genormaliseerd (voor lange sleutelwoorden, > 3 tekens)
    protocol_norm = raw.replace(" ", "").replace("-", "").replace("_", "")
    # Ruwe versie met spaties bewaard: spaties/koppeltekens/underscores als woordgrenzen
    protocol_bound = raw.replace("-", " ").replace("_", " ")
    for sleutel, weging, forceer_3d in PROTOCOL_SLEUTELWOORDEN:
        sleutel_norm = sleutel.replace("-", "").replace("_", "")
        if len(sleutel_norm) <= 3:
            # Woordgrens voor korte sleutelwoorden (T1, T2, IR, PD, ADC, DWI ...)
            pattern = r'(?<![A-Z0-9])' + re.escape(sleutel_norm) + r'(?![A-Z0-9])'
            if re.search(pattern, protocol_bound):
                return weging, forceer_3d
        else:
            if sleutel_norm in protocol_norm:
                return weging, forceer_3d
    return None


# ---------------------------------------------------------------------------
# DTI: aantal richtingen bepalen
# ---------------------------------------------------------------------------

def _dti_richtingen(ds):
    """Geeft het aantal diffusie-richtingen terug, of None als unknown.

    Probeert achtereenvolgens:
      - (0018,9089) DiffusionGradientOrientation  (geneste SQ -> items tellen)
      - (0018,9076) DiffusionGradientDirectionSequence (idem)
    """
    for tag in ((0x0018, 0x9089), (0x0018, 0x9076)):
        waarde = zoek_tag(ds, tag)
        if waarde is not None:
            try:
                # Als het een Sequence is, tel de items.
                return len(waarde)
            except TypeError:
                pass
    return None


def _dti_weging(ds):
    """Bouw de DTI-weging-string, inclusief richtingen als die bekend zijn."""
    n = _dti_richtingen(ds)
    if n is not None:
        return f"DTI_{n}dir"
    return "DTI"


def _mrs_weging(protocol_norm):
    """Bouw de spectroscopie-weging: type (SVS/CSI/MRS) + techniek (PRESS/STEAM/sLASER).

    Type-detectie op trefwoord in protocolnaam (meest-specifiek eerst).
    Techniek-detectie idem; als geen techniek gevonden -> alleen het type.
    """
    # Type
    if "SVS" in protocol_norm:
        mrs_type = "SVS"
    elif "CSI" in protocol_norm or "MRSI" in protocol_norm:
        mrs_type = "CSI"
    else:
        mrs_type = "MRS"

    # Techniek
    if "SLASER" in protocol_norm or "LASER" in protocol_norm:
        techniek = "sLASER"
    elif "STEAM" in protocol_norm:
        techniek = "STEAM"
    elif "PRESS" in protocol_norm:
        techniek = "PRESS"
    else:
        techniek = None

    return f"{mrs_type}-{techniek}" if techniek else mrs_type


def _mdixon_weging(img_type):
    """Bepaal de mDixon reconstructie-subtype uit ImageType.

    Philips mDixon levert aparte reconstructies: water, vet, in-fase en
    uit-fase. Als ImageType er geen of meer dan één aangeeft, gebruiken
    we 'mDixon-ALL'.
    """
    gevonden = []
    if "WATER" in img_type:
        gevonden.append("W")
    if "FAT" in img_type:
        gevonden.append("F")
    if "IN_PHASE" in img_type or "INPHASE" in img_type:
        gevonden.append("IP")
    if "OUT_PHASE" in img_type or "OUTPHASE" in img_type or "OUT-PHASE" in img_type:
        gevonden.append("OP")
    if "DIXON" in img_type and not gevonden:
        return "mDixon-ALL"
    if len(gevonden) == 1:
        return f"mDixon-{gevonden[0]}"
    return "mDixon-ALL"


def onderdeel_weging(ds):
    """
    Bepaalt de acquisitie-weging uit DICOM-metadata.

    Stap 1 — ProtocolName: als die een bekend sleutelwoord bevat, direct gebruiken.
    Stap 2 — Tag-gebaseerde logica (van specifiek naar algemeen):
        a. ADC (afgeleid beeld)
        b. DWI-subtypes: DWIBS / DTI / Multi Shot / TSE DWI / DWI
        c. EPI zonder diffusie (bv. fMRI)
        d. Inversion Recovery: STIR / FLAIR / IR
        e. Gradient Echo (FFE): T1FFE / T2FFE / bFFE / mFFE / mDixon
        f. Spin Echo / TSE: T1 / T2 / PD
        g. Fallback op TR/TE

    3D-prefix: MRAcquisitionType == '3D'  ->  '3D' voor de weging.
    DWI-subtypes krijgen nooit een 3D-prefix (3D DWI bestaat niet klinisch).
    """
    # --- Tags inlezen --------------------------------------------------------
    # zoek_tag wordt gebruikt zodat geneste waarden (Enhanced MR) ook gevonden worden.
    tr       = _getal(zoek_tag(ds, "RepetitionTime", (0x0018, 0x0080)))
    te       = _getal(zoek_tag(ds, "EchoTime", (0x0018, 0x0081)))
    ti       = _getal(zoek_tag(ds, "InversionTime", (0x0018, 0x0082)))
    scan_seq = str(zoek_tag(ds, "ScanningSequence", (0x0018, 0x0020)) or "").upper()
    etl      = _getal(zoek_tag(ds, "EchoTrainLength", (0x0018, 0x0091)))
    acq_type = str(zoek_tag(ds, "MRAcquisitionType", (0x0018, 0x0023)) or "").upper()
    b_waarde = _getal(zoek_tag(ds, "DiffusionBValue", (0x0018, 0x9087)))
    img_type = str(ds.get("ImageType", "")).upper()

    is_3d  = acq_type == "3D"
    is_ep  = "EP" in scan_seq
    is_gr  = "GR" in scan_seq
    is_se  = "SE" in scan_seq
    is_ir  = "IR" in scan_seq or (ti is not None and ti > 0)
    is_tse = is_se and etl is not None and etl > 1

    def naam(weging, geen_3d=False):
        if is_3d and not geen_3d:
            return f"3D{weging}"
        return weging

    # =========================================================================
    # STAP 0 — VANE-varianten (voor normale protocol-lookup, eigen logica)
    # =========================================================================
    protocol_orig = str(ds.get("ProtocolName", ""))      # originele hoofdletters
    series_orig   = str(ds.get("SeriesDescription", "")) # originele hoofdletters
    protocol_raw  = protocol_orig.upper()
    series_raw    = series_orig.upper()
    # Strip Philips WIP-prefix voor matching
    protocol_norm = protocol_raw.replace(" ", "").replace("-", "").replace("_", "")
    if protocol_norm.startswith("WIP"):
        protocol_norm = protocol_norm[3:]
    series_norm = series_raw.replace(" ", "").replace("-", "").replace("_", "")
    if series_norm.startswith("WIP"):
        series_norm = series_norm[3:]

    # Voor woordgrens-matching van korte sleutelwoorden: bewaar underscores
    # zodat 'T1' matcht in 'T1_native' maar niet in 'T1NATIVE'
    protocol_bound = protocol_raw.replace(" ", "").replace("-", "").upper()
    if protocol_bound.startswith("WIP"):
        protocol_bound = protocol_bound[3:]
    series_bound = series_raw.replace(" ", "").replace("-", "").upper()
    if series_bound.startswith("WIP"):
        series_bound = series_bound[3:]

    # SURVEY / LOCALIZER -> originele naam behouden (EERSTE check, vóór VIEW!)
    _survey_trefwoorden = ("SURVEY", "PLANSCAN", "LOCALIZER", "SCOUT", "MOBIVIEW",
                           "MINIP", "TRANCE",
                           "SMARTBRAIN", "SMARTKNEE", "SMARTSPINE",
                           "SMARTHEART", "SMARTBREAST")
    _is_survey = any(k in protocol_norm or k in series_norm
                     for k in _survey_trefwoorden)
    if not _is_survey:
        _loc_pattern = r'(?<![A-Z0-9])LOC(?![A-Z0-9])'
        _is_survey = (re.search(_loc_pattern, protocol_bound) is not None or
                      re.search(_loc_pattern, series_bound) is not None)
    if _is_survey:
        serie_strip = series_orig.strip()
        prot_strip  = protocol_orig.strip()
        # Als seriebeschrijving alleen een oriëntatie-woord is (ax, sag, cor, tra),
        # gebruik dan de protocolnaam die meer context bevat.
        _oriëntatie = {"AX", "SAG", "COR", "TRA", "AXIAL", "SAGITTAL",
                       "CORONAL", "TRANSVERSAL", "WSAG", "WCOR", "WTRA"}
        if serie_strip.upper() in _oriëntatie and prot_strip:
            orig = prot_strip
        else:
            orig = serie_strip or prot_strip
        return orig if orig else "Survey"

    # EPI detectie ook op SeriesDescription als ScanningSequence geen EP bevat
    # Gebruik woordgrens voor 'EPI' zodat 'TSE PI' -> 'TSEPI' niet foutief matcht
    if not is_ep:
        if any(k in series_norm for k in ("FEEPI", "GREEPI")):
            is_ep = True
        elif re.search(r'(?<![A-Z0-9])EPI(?![A-Z0-9])',
                       series_raw.replace("-", " ").replace("_", " ")):
            is_ep = True

    # MRE (MR Elastography): techniek-suffix uit protocolnaam of ScanningSequence
    if "MRE" in protocol_norm or "ELASTOGRAPH" in protocol_norm:
        if "EPI" in protocol_norm or is_ep:
            return "MRE-EPI"
        if "TSE" in protocol_norm or (is_se and etl is not None and etl > 1):
            return "MRE-TSE"
        if "SE" in protocol_norm or is_se:
            return "MRE-SE"
        if "GRE" in protocol_norm or "FFE" in protocol_norm or is_gr:
            return "MRE-GRE"
        return "MRE"

    # PSIR met oriëntatie-suffix (2ch, 3ch, 4ch, SAX)
    if "PSIR" in protocol_norm or "PSIR" in series_norm:
        src = protocol_norm + series_norm
        if "4CH" in src:
            return naam("PSIR-4ch")
        if "3CH" in src:
            return naam("PSIR-3ch")
        if "2CH" in src:
            return naam("PSIR-2ch")
        if "SAX" in src or "SA" in src:
            return naam("PSIR-SAX")
        # Geen oriëntatie -> gewone PSIR via protocol-lookup

    # Cine (cardiac): view-suffix op basis van protocolnaam
    if "CINE" in protocol_norm:
        if "2CH" in protocol_norm:
            return "Cine2ch"
        if "3CH" in protocol_norm:
            return "Cine3ch"
        if "4CH" in protocol_norm:
            return "Cine4ch"
        if "SAX" in protocol_norm or "SA" in protocol_norm:
            return "CineSAX"
        return "Cine"

    # SyntAc (Synthetic MRI): 2D -> SyntAc, 3D -> 3dSyntAc
    # DWI-MB: Multi-Band DWI (simultaneous multi-slice)
    if ("DWI" in protocol_norm or "DWI" in series_norm) and \
       (re.search(r'(?<![A-Z0-9])MB(?![A-Z0-9])', protocol_bound) or
        re.search(r'(?<![A-Z0-9])MB(?![A-Z0-9])', series_bound)):
        return "DWI-MB"

    if "SYNTAC" in protocol_norm:
        return "3dSyntAc" if is_3d else "SyntAc"

    # DSC / PWI (Dynamic Susceptibility Contrast / Perfusion Weighted Imaging)
    if any(k in protocol_norm for k in ("DSC", "PWI")) or ("PERFUSION" in img_type and is_ep):
        return "T2*DSC"

    # ASL (Arterial Spin Labeling): 2D -> ASL, 3D -> 3dASL
    if "ASL" in protocol_norm or "ASL" in img_type:
        return "3dASL" if is_3d else "ASL"

    # TOF (Time of Flight): 2D -> TOF, 3D -> 3dTOF
    if "TOF" in protocol_norm:
        return "3dTOF" if is_3d else "TOF"

    # PCA (Phase Contrast Angiography): 2D -> PCA, 3D -> 3dPCA
    if any(k in protocol_norm for k in ("PCMRA", "PHASEC", "PCA", "PHASECONTRAST")):
        return "3dPCA" if is_3d else "PCA"

    # MRA (generieke MR Angiografie): 2D -> MRA, 3D -> 3dMRA
    if "MRA" in protocol_norm:
        return "3dMRA" if is_3d else "MRA"

    # SWI (Susceptibility Weighted Imaging): altijd 3D -> SWIp
    if "SWI" in protocol_norm or "SWI" in img_type:
        return "SWIp"

    # QSM (Quantitative Susceptibility Mapping): altijd 3D, alleen protocol naam
    if "QSM" in protocol_norm or "QSM" in img_type:
        return "QSM"

    # --- Mapping sequences ---------------------------------------------------
    if "T1MAP" in protocol_norm or "T1MAPPING" in protocol_norm:
        return "T1map"

    if "T1RHO" in protocol_norm:
        if "BFFE" in protocol_norm or "SSFP" in protocol_norm or (is_gr and "SS" in str(ds.get("SequenceVariant", "")).upper()):
            return "T1rho-bFFE"
        if "TSE" in protocol_norm or (is_se and etl is not None and etl > 1):
            return "T1rho-TSE"
        if "FFE" in protocol_norm or is_gr:
            return "T1rho-FFE"
        return "T1rho"

    if any(k in protocol_norm for k in ("T2STARMAP", "T2STARMAPPING",
                                        "R2STAR", "T2STAR", "R2_STAR")):
        return "T2*map-mFFE"

    if "T2MAP" in protocol_norm or "T2MAPPING" in protocol_norm:
        # Techniek bepalen uit protocolnaam of ScanningSequence
        if "GRASE" in protocol_norm or (is_gr and is_se):
            return "T2map-GRaSE"
        if "TSE" in protocol_norm or (is_se and etl is not None and etl > 1):
            return "T2map-TSE"
        return "T2map"

    # MRS / SVS / CSI (Spectroscopie)
    # SOPClassUID 1.2.840.10008.5.1.4.1.1.4.2 = MR Spectroscopy Storage
    sop_class = str(ds.get("SOPClassUID", ""))
    is_spectro = (sop_class == "1.2.840.10008.5.1.4.1.1.4.2" or
                  any(k in protocol_norm for k in ("MRS", "SVS", "CSI", "MRSI",
                                                    "SPECTRO", "STEAM", "PRESS",
                                                    "SLASER")))
    if is_spectro:
        return _mrs_weging(protocol_norm)

    # DCE (Dynamic Contrast Enhanced): altijd 3dT1FFE_dyn
    if "DCE" in protocol_norm or any(k in img_type for k in ("DYNAMIC", "TIMEPOINT")):
        return "3dT1FFE_dyn"

    # 4D VANE / 4D Freebreathing
    if "4DVANE" in protocol_norm or "4DFREEBREATHING" in protocol_norm or "4DFB" in protocol_norm:
        return "4dFB"

    # VIEW-sequenties (BrainVIEW, SpineVIEW, ProstateVIEW etc.)
    # Naam dynamisch ophalen: woord direct vóór 'VIEW' wordt bewaard.
    _src_voor_view = protocol_raw or series_raw
    _view_match = re.search(r'([A-Za-z]+)VIEW', _src_voor_view, re.IGNORECASE)
    if _view_match or "VIEW" in protocol_norm or "VIEW" in series_norm:
        if "NERVEVIEW" in protocol_norm or "NERVEVIEW" in series_norm:
            return "3dNerveView"
        if "VISTA" in protocol_norm or "VISTA" in series_norm:
            view_naam = "View"
        elif _view_match:
            # Bewaar het prefix-woord met juiste hoofdletters: Brain, Spine, Prostate etc.
            prefix = _view_match.group(1)
            view_naam = prefix.capitalize() + "View"
        else:
            view_naam = "View"
        # Weging bepalen uit TR/TE
        view_weging = "T2"
        if tr is not None and te is not None:
            if tr < TR_KORT:
                view_weging = "T1"
            elif te > TE_LANG:
                view_weging = "T2"
        return f"3D{view_weging}-{view_naam}"

    # 3D VANE: check op mDixon in ImageType voor suffix
    if "3DVANE" in protocol_norm:
        if any(k in img_type for k in ("DIXON", "WATER", "FAT", "IN_PHASE",
                                        "INPHASE", "OUT_PHASE", "OUTPHASE")):
            return "3DVane_mDix"
        return "3DVane"

    # =========================================================================
    # STAP 1 — ProtocolName heeft voorrang op alle tag-logica
    # =========================================================================
    protocol_hit = protocol_naam_weging(ds)
    if protocol_hit is not None:
        weging, forceer_3d = protocol_hit
        # DTI: voeg richtingen toe, maar vereist >= 6 richtingen voor echte tensor.
        # Bij < 6 richtingen terugvallen op DWI ondanks 'DTI' in protocolnaam.
        if weging == "DTI":
            n = _dti_richtingen(ds)
            if n is not None and n < 6:
                weging = "DWI"
            else:
                weging = _dti_weging(ds)
        # mDixon: verfijn naar W/F/IP/OP/ALL op basis van ImageType.
        if weging == "mDixon":
            weging = _mdixon_weging(img_type)
        # FLAIR: TE < 50ms = T1 FLAIR, TE >= 50ms = gewone (T2) FLAIR
        if weging == "FLAIR" and te is not None and te < 50:
            weging = "T1FLAIR"
        # ADC is altijd een afgeleid beeld, nooit 3D-prefix
        _nooit_3d = {"ADC", "DWI", "DTI", "DWIBS", "TSEDWI", "IRIS-DWI",
                     "fMRI", "EPI", "SWIp", "QSM"}
        if weging in _nooit_3d or weging.startswith("DTI_"):
            return weging
        if forceer_3d or is_3d:
            return f"3D{weging}"
        return weging

    # =========================================================================
    # STAP 2 — Tag-gebaseerde detectie
    # =========================================================================

    # --- a. ADC (afgeleid/berekend beeld) ------------------------------------
    if "ADC" in img_type or "APPARENT_DIFFUSION_COEFF" in img_type:
        return "ADC"

    # --- b. DWI-subtypes -----------------------------------------------------
    is_diffusie = (b_waarde is not None and b_waarde > 0) or "DIFFUSION" in img_type
    if is_diffusie:
        # DWIBS: EPI-diffusie + InversionTime (STIR-achtige achtergrondonderdrukking)
        if ti is not None and ti > 0:
            return "DWIBS"
        # DTI: minimaal 6 richtingen vereist voor diffusion tensor
        # < 6 richtingen = DWI met meerdere b-waarden, geen echte tensor
        n_richtingen = _dti_richtingen(ds)
        if n_richtingen is not None and n_richtingen >= 6:
            return _dti_weging(ds)
        # Afgeleide ADC-varianten (dadc, eadc, dADC etc.) -> ADC, geen TSEDWI
        _naam_raw = (str(ds.get("SeriesDescription", "")) +
                     str(ds.get("ProtocolName", ""))).upper()
        if "ADC" in _naam_raw:
            return "ADC"
        # Serie-beschrijving is een b-waarde (b1000, b0, b800 etc.) -> standaard DWI
        if re.match(r'^B\d+', _naam_raw.strip()):
            return "DWI"
        # TSE DWI: spin echo readout (geen EP) met b-waarde
        if is_se and not is_ep:
            return "TSEDWI"
        # Standaard DWI (single shot EPI)
        return "DWI"

    # --- c. EPI zonder diffusie ----------------------------------------------
    if is_ep:
        # fMRI: ImageType bevat FMRI of BOLD
        if "FMRI" in img_type or "BOLD" in img_type:
            return "fMRI"
        # ASL: ImageType bevat ASL of PERFUSION
        if "ASL" in img_type or "PERFUSION" in img_type:
            return "ASL"
        # SE-EPI: spin echo readout (SE + EP)
        if is_se:
            return "SE-EPI"
        # GRE-EPI: TFE-EPI vs FFE-EPI niet te onderscheiden uit standaard tags
        # -> unknown, handmatige popup
        return "unknown"

    # --- d. Inversion Recovery -----------------------------------------------
    if is_ir:
        # IR-prepped gradient echo (bv. MP-RAGE, IR-TFE): TI aanwezig + GRE readout
        # -> dit is 3D T1 TFE, geen gewone IR-sequentie
        if is_gr:
            return naam("T1TFE")

        # PSIR: ImageType bevat "PSIR", of "PHASE" gecombineerd met TI
        if "PSIR" in img_type:
            return naam("PSIR")
        if "PHASE" in img_type and ti is not None and ti > 0:
            return naam("PSIR")

        # DIR: ImageType bevat "DIR" of dubbele inversie-aanwijzing
        if "DIR" in img_type:
            return naam("DIR")

        # STIR / FLAIR op basis van TI-drempelwaarden
        if ti is not None and ti > 0:
            if ti < TI_STIR:
                return naam("STIR")
            if ti >= TI_FLAIR:
                # Single Shot FLAIR: ETL > 70
                if etl is not None and etl > 70:
                    return naam("FLAIR-SSh")
                # TE < 50ms = T1 FLAIR, anders gewone (T2) FLAIR
                if te is not None and te < 50:
                    return naam("T1FLAIR")
                return naam("FLAIR")

        # IR met unknown TI of tussenliggende waarde zonder verdere info
        return naam("IR")

    # --- e0. GRaSE: ScanningSequence bevat zowel GR als SE -------------------
    if is_gr and is_se:
        return naam("GRaSE")

    # --- e. Gradient Echo -> FFE-naamgeving (Philips) ------------------------
    if is_gr:
        # mDixon: ImageType bevat DIXON, WATER, FAT, IN_PHASE of OUT_PHASE
        if any(k in img_type for k in ("DIXON", "WATER", "FAT", "IN_PHASE",
                                        "INPHASE", "OUT_PHASE", "OUTPHASE")):
            return naam(_mdixon_weging(img_type))
        # bFFE (balanced / SSFP): SequenceVariant bevat 'SS' (steady state)
        seq_variant = str(ds.get("SequenceVariant", "")).upper()
        if "SS" in seq_variant:
            return naam("bFFE")
        # mFFE: meerdere echo's (EchoTrainLength > 1 op GRE)
        if etl is not None and etl > 1:
            return naam("mFFE")
        # T1FFE: TE < 10ms is voldoende (flip angle niet betrouwbaar, range 10-45°)
        # T2FFE: TE > 15ms én flip angle < 25° vereist
        flip = _getal(ds.get("FlipAngle"))      # (0018,1314)
        if te is None:
            return naam("unknown")
        if te < GRE_TE_T1:
            return naam("T1FFE")
        if te > GRE_TE_T2:
            if flip is not None and flip < GRE_FA_T2:
                return naam("T2FFE")
            return naam("unknown")
        # TE tussen 10-15 ms: altijd unknown -> popup
        return naam("unknown")

    # --- f. Spin Echo / Turbo Spin Echo --------------------------------------
    if is_se:
        if te is None or tr is None:
            return naam("unknown")
        # mDixon TSE: ImageType bevat Dixon-sleutelwoorden op SE-sequentie
        if any(k in img_type for k in ("DIXON", "WATER", "FAT", "IN_PHASE",
                                        "INPHASE", "OUT_PHASE", "OUTPHASE")):
            return naam("T2-mDix")
        # SSh (single shot TSE / HASTE): ETL > 70
        if etl is not None and etl > 70:
            return "T2SSh"
        # T2-MRCP: extreem lange TE (> 400 ms) op SE/TSE
        if te > 400:
            return "T2-MRCP"
        if te > TE_LANG:
            return naam("T2")
        if te < TE_KORT and tr < TR_KORT:
            return naam("T1")       # korte TE én korte TR -> T1
        # 3D TSE met korte TR: TE tot 45ms nog T1 (bv. BrainVIEW T1W TR=700 TE=35)
        if is_3d and tr < TR_KORT and te < 45:
            return naam("T1")
        # 30-55 ms of lange TR -> PD
        if tr > TR_LANG:
            return naam("PD")
        # TE kort maar TR niet kort genoeg voor T1, of middellange TE -> unknown
        return naam("unknown")

    # --- g. Fallback: ScanningSequence ontbreekt of unknown -----------------
    if tr is None or te is None:
        return naam("unknown")
    if te > TE_LANG:
        return naam("T2")
    if te < TE_KORT and tr < TR_KORT:
        return naam("T1")
    if tr > TR_LANG:
        return naam("PD")
    return naam("mixed")


# Wegingen waarbij het script er NIET zeker van is -> popup voor handmatige invoer.
ONZEKERE_WEGINGEN = {"mixed", "unknown", "3Dmixed", "3Dunknown"}


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
        if val is not None:
            # ds.get() geeft een DataElement terug; extraheer de waarde.
            # str(DataElement) bevat de VR (bv. 'CS: SENSE') wat fout-matches geeft.
            if hasattr(val, "value"):
                val = val.value
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


def _is_recon(ds):
    """True als dit een scanner-reconstructie is (MPR, subtractie, MIP, etc.).

    Philips private tags:
      (2001,101D) ReconstructionNumber: > 1 betekent een afgeleide reconstructie
      (2001,107B) AcquisitionNumber:    aanvullend ter controle

    Standaard ImageType: eerste waarde 'DERIVED' bevestigt het.
    """
    # ReconstructionNumber tag is te breed op Philips (ook normale series > 1)
    # -> alleen ImageType DERIVED en expliciete MIP/MPR protocol namen gebruiken

    # Only flag as recon based on explicit protocol/series name keywords.
    # ImageType checks removed — too many false positives on non-Philips scanners.
    protocol = str(ds.get("ProtocolName", "")).upper().replace(" ", "").replace("-", "").replace("_", "")
    series   = str(ds.get("SeriesDescription", "")).upper().replace(" ", "").replace("-", "").replace("_", "")
    # Use word-boundary style check: keyword must not be embedded in a longer word
    # e.g. "PRECISE" should NOT match "RECON" even if letters overlap
    for k in ("MIP", "MPR", "MINIP", "RECON"):
        for src in (protocol, series):
            idx = src.find(k)
            while idx != -1:
                before = idx == 0 or not src[idx-1].isalpha()
                after  = idx+len(k) >= len(src) or not src[idx+len(k)].isalpha()
                if before and after:
                    return True
                idx = src.find(k, idx+1)

    return False


def _heeft_fatsat(ds):
    """True als de sequentie een spectrale vet-onderdrukking heeft (SPAIR / SPIR).

    Controleert achtereenvolgens:
      1. ProtocolName op SPAIR, SPIR, FATSAT, of eindigt op 'FS'
      2. SpectrallySelectedSuppression (0018,9025) = FAT of FAT_AND_WATER
      3. SequenceVariant (0018,0021) bevat 'SP' (spectral presaturation)

    STIR en mDixon worden NIET als fatsat beschouwd: STIR gebruikt een
    inversie-puls en mDixon scheidt water/vet rekenkundig.
    """
    protocol = str(ds.get("ProtocolName", "")).upper().replace(" ", "").replace("-", "").replace("_", "")
    if any(k in protocol for k in ("SPAIR", "SPIR", "FATSAT")):
        return True
    # Protocollen die eindigen op 'FS' (bv. 'T2FS', 'BRAINFS')
    if protocol.endswith("FS"):
        return True

    sss = str(zoek_tag(ds, "SpectrallySelectedSuppression", (0x0018, 0x9025)) or "").upper()
    if "FAT" in sss:
        return True

    return False


def _bwaarde_uit_naam(ds):
    """Geeft 'b1000', 'b0' etc. terug als de seriebeschrijving een b-waarde bevat.

    Matcht patronen als 'b1000', 'DWI b800', '50 600 1000' (meerdere b-waarden).
    Retourneert None als geen b-waarde gevonden.
    """
    for veld in ("SeriesDescription", "ProtocolName"):
        naam = str(ds.get(veld, ""))
        # Enkelvoudige b-waarde: b1000, b0, B800
        m = re.search(r'\bb(\d+)\b', naam, re.IGNORECASE)
        if m:
            return f"b{m.group(1)}"
        # Meerdere b-waarden: '50 600 1000' (3 losse getallen)
        getallen = re.findall(r'\b(\d+)\b', naam)
        if len(getallen) >= 2 and all(int(g) < 5000 for g in getallen):
            return "b" + "_".join(getallen)
    return None


def _heeft_bb(ds):
    """True als de ProtocolName '_BB' of 'BB' bevat (Black Blood techniek)."""
    protocol = str(ds.get("ProtocolName", "")).upper().replace(" ", "").replace("-", "").replace("_", "")
    return "BB" in protocol


def _heeft_mt(ds):
    """True als de sequentie een Magnetization Transfer voorbereiding heeft.

    Controleert:
      1. ProtocolName op MT of MTR
      2. MagnetizationTransfer (0018,9020) = ON
      3. SequenceVariant (0018,0021) bevat MTC
    """
    protocol = str(ds.get("ProtocolName", "")).upper().replace(" ", "").replace("-", "").replace("_", "")
    if "MTR" in protocol or (protocol.endswith("MT") or "_MT" in str(ds.get("ProtocolName", "")).upper()):
        return True

    # Tag-gebaseerde MT-detectie is te breed op Philips (veel sequenties hebben
    # MagnetizationTransfer=ON zonder bewuste MT-puls) -> alleen protocolnaam.
    return False


def maak_naam(ds):
    undersampling = onderdeel_undersampling(ds)
    weging = onderdeel_weging(ds)

    # Als de weging de originele seriebeschrijving/protocolnaam is (keep-original),
    # geef hem dan direct terug zonder prefix of suffix.
    _serie_orig  = str(ds.get("SeriesDescription", "")).strip()
    _prot_orig   = str(ds.get("ProtocolName", "")).strip()
    if weging and (weging == _serie_orig or weging == _prot_orig):
        return _veilige_mapnaam(weging)
    # Geen fs/mt suffix op DWI-familie, EPI of afgeleide beelden
    _geen_suffix_prefixen = ("DWI", "DTI", "ADC", "DWIBS", "TSEDWI", "IRIS-DWI",
                             "MultiShotDWI", "EPI", "fMRI", "ASL", "3dASL",
                             "IVIM", "unknown", "3Dunknown", "mixed", "3Dmixed",
                             "T2SSh", "4dFB", "GRaSE", "T2-MRCP", "3DT2-MRCP", "MRS", "SVS",
                             "CSI", "T2map", "T1map", "T2*map", "T1rho", "QSM",
                             "SWIp", "PCA", "TOF", "MRA", "MRE")
    _heeft_geen_suffix = any(weging.startswith(p) for p in _geen_suffix_prefixen)
    if _heeft_fatsat(ds) and not _heeft_geen_suffix:
        weging = weging + "fs"
    # MT-suffix uitgeschakeld op verzoek (te veel valse positieven)
    # if _heeft_mt(ds) and not _heeft_geen_suffix:
    #     weging = weging + "mt"

    # fMRI/EPI gebruikt altijd SENSE, nooit CS-SENSE
    if weging in {"fMRI", "EPI"} and undersampling == "CS":
        undersampling = "s"

    bb       = ["BB"] if _heeft_bb(ds) else []
    tijd     = onderdeel_acquisitietijd(ds)
    dikte    = onderdeel_slicethickness(ds)

    # B-waarde uit seriebeschrijving toevoegen voor DWI-families
    _dwi_families = ("DWI", "DTI", "DWIBS", "TSEDWI", "IRIS-DWI")
    _bval = _bwaarde_uit_naam(ds) if any(weging.startswith(f) for f in _dwi_families) else None
    if _bval:
        bb = [_bval] + bb  # b-waarde vóór BB-suffix

    is_recon = _is_recon(ds)

    # Geen parallel imaging -> naam begint direct met de weging
    if undersampling == "noPI":
        if is_recon:
            return SCHEIDINGSTEKEN.join([weging] + bb + ["recon"])
        return SCHEIDINGSTEKEN.join([weging] + bb + [tijd, dikte])

    # Post-processed reconstructies (MPR, subtractie, MIP): geen tijd/dikte
    if _is_recon(ds):
        return SCHEIDINGSTEKEN.join([undersampling, weging] + bb + ["recon"])

    return SCHEIDINGSTEKEN.join([undersampling, weging] + bb + [tijd, dikte])


# SOP Class UID van een gewoon MR-beeld ("MR Image Storage").
# Alleen deze bestanden bevatten de TR/TE/SliceThickness die we nodig hebben.
# Presentation States, DICOMDIR en Philips-eigen raw-objecten hebben een
# andere SOP Class en worden zo netjes overgeslagen.
MR_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.4"

# Enhanced MR: meerdere frames per bestand, TR/TE genest in functional groups.
ENHANCED_MR_STORAGE = {
    "1.2.840.10008.5.1.4.1.1.4.1",  # Enhanced MR Image Storage
    "1.2.840.10008.5.1.4.1.1.4.3",  # Enhanced Color MR Image Storage
}


def is_mr_beeld(ds):
    """True als dit een echt MR-beeld is (klassiek of Enhanced MR)."""
    if str(ds.get("Modality", "")) != "MR":
        return False
    sop = str(ds.get("SOPClassUID", ""))
    if sop == MR_IMAGE_STORAGE:
        return ds.get("RepetitionTime") is not None and ds.get("EchoTime") is not None
    if sop in ENHANCED_MR_STORAGE:
        # TR/TE zitten genest -> zoek_tag doorzoekt de hele boom
        tr = zoek_tag(ds, "RepetitionTime", (0x0018, 0x0080))
        te = zoek_tag(ds, "EchoTime", (0x0018, 0x0081))
        return tr is not None and te is not None
    return False


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
        "studie_uid": str(ds.get("StudyInstanceUID", "")),
        "serienummer": str(ds.get("SeriesNumber", "")),
        "seriebeschrijving": str(ds.get("SeriesDescription", "")),
        "patient_naam": str(ds.get("PatientName", "")),
        "patient_id": str(ds.get("PatientID", "")),
        "studie_datum": str(ds.get("StudyDate", "")),
        "studie_beschrijving": str(ds.get("StudyDescription", "")),
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
    Na groepering: recon-series erven de undersampling van de bijbehorende
    niet-recon serie binnen hetzelfde onderzoek.
    """
    series = {}
    for r in resultaten:
        # Prefer SeriesInstanceUID; fall back to (StudyUID, SeriesNumber) when absent
        uid = r.get("serie_uid") or ""
        if not uid:
            uid = f"{r.get('studie_uid','')}#{r.get('serienummer','')}" or r["bestand"]
            r = dict(r)          # copy so we don't mutate the original
            r["serie_uid"] = uid  # store composite key so viewer can find all files
        if uid not in series:
            eerste = dict(r)
            eerste["aantal_slices"] = 1
            series[uid] = eerste
        else:
            series[uid]["aantal_slices"] += 1

    serie_lijst = list(series.values())

    # -----------------------------------------------------------------------
    # Detecteer afgeleide series op basis van serie-nummer patroon:
    # X01 = basereeks, X02/X03/... = afgeleid van X01 in dezelfde studie.
    # Afgeleide series krijgen naam van de basereeks zonder tijd/dikte + _recon.
    # -----------------------------------------------------------------------

    # Bouw index: (studie_uid, base_nr) -> basereeks-resultaat
    base_index = {}
    for r in serie_lijst:
        studie = r.get("studie_uid", "")
        try:
            nr = int(r.get("serienummer", 0))
        except (ValueError, TypeError):
            continue
        if nr % 100 == 1:                       # X01 = basereeks
            base_nr = (nr // 100) * 100 + 1
            base_index[(studie, base_nr)] = r

    # Pas afgeleide series aan
    for r in serie_lijst:
        studie = r.get("studie_uid", "")
        try:
            nr = int(r.get("serienummer", 0))
        except (ValueError, TypeError):
            continue
        if nr % 100 == 0 or nr % 100 == 1:
            continue                             # basereeks, niet aanpassen
        base_nr = (nr // 100) * 100 + 1
        # Behoud specifieke namen voor ADC/DWI/spectro-varianten
        naam_huidig = r.get("naam", "")
        weging_huidig = r.get("weging", "")
        orig = (r.get("seriebeschrijving", "") + r.get("origineel_protocol", "")).upper()
        _behoud_prefixen = ("ADC", "DWI", "DTI", "DWIBS", "TSEDWI", "MRS",
                            "SVS", "CSI", "T2map", "T1map", "T2*map")
        if any(weging_huidig.startswith(p) for p in _behoud_prefixen):
            continue
        if "ADC" in orig or "EADC" in orig or "DADC" in orig:
            # Specifieke ADC-variant namen behouden
            us = r.get("undersampling", "")
            prefix = f"{us}_" if us and us != "noPI" else ""
            if "EADC" in orig or "EADC" in orig.replace(" ", ""):
                r["naam"] = f"{prefix}eADC"
            elif "DADC" in orig:
                r["naam"] = f"{prefix}dADC"
            else:
                r["naam"] = f"{prefix}ADC"
            continue

        basis = base_index.get((studie, base_nr))
        if basis is None:
            continue
        # Gebruik naam van basereeks: undersampling + weging + _recon
        basis_naam = basis.get("naam", "")
        # Strip tijd en dikte van de basisnaam (laatste 2 _-onderdelen)
        delen = basis_naam.split("_")
        # Verwijder tijds- en dikte-componenten achteraan
        while delen and (delen[-1].endswith("mm") or
                         delen[-1].endswith("s") or
                         delen[-1] == "noTime" or
                         delen[-1] == "noThick"):
            delen.pop()
        prefix = "_".join(delen) if delen else basis_naam
        r["naam"] = f"{prefix}_recon"
        r["undersampling"] = basis.get("undersampling", r.get("undersampling", ""))

    return serie_lijst


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
# POPUP VOOR HANDMATIGE NAAM (bij unknowne techniek, bv. DWI)
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
             "(unknowne acquisitietechniek, bv. DWI).",
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


def schrijf_naar_output(resultaten_per_bestand, output_map, bron_map=None,
                        alle_bestanden=None):
    """Kopieer de volledige bronmap naar output_map met behoud van de mapstructuur.

    - Alle bestanden (DICOM én niet-DICOM) worden gekopieerd.
    - Voor MR-beelden wordt alleen tag (0018,1030) ProtocolName aangepast.
    - Alle andere bestanden (.ExamCard, README, etc.) worden ongewijzigd gekopieerd.

    'resultaten_per_bestand' = lijst van per-bestand dicts met gegenereerde namen.
    'bron_map' = pad van de bronmap voor relatieve padberekening.
    'alle_bestanden' = alle bestanden in de bronmap (incl. niet-DICOM).
    """
    import shutil
    os.makedirs(output_map, exist_ok=True)

    # Bouw een lookup: bestandspad -> gegenereerde naam (alleen MR-beelden)
    naam_per_bestand = {r["bestand"]: r["naam"] for r in resultaten_per_bestand}

    # Kopieer alle bestanden, ook niet-DICOM
    if alle_bestanden and bron_map:
        for bronpad in alle_bestanden:
            if bronpad in naam_per_bestand:
                continue  # MR-beelden worden hieronder apart behandeld
            try:
                rel_pad = os.path.relpath(bronpad, bron_map)
            except ValueError:
                rel_pad = os.path.basename(bronpad)
            doelpad = os.path.join(output_map, rel_pad)
            os.makedirs(os.path.dirname(doelpad), exist_ok=True)
            shutil.copy2(bronpad, doelpad)

    totaal_bestanden = 0
    overgeslagen = 0

    for r in resultaten_per_bestand:
        bronpad = r["bestand"]
        serie_naam = r["naam"]

        # Onzekere weging -> popup
        if naam_is_onzeker(r):
            print(f"  ? Serie {r.get('serienummer','?')} "
                  f"('{r.get('seriebeschrijving','')}') niet automatisch "
                  f"herkend -> popup geopend...")
            handmatig = vraag_naam_popup(r)
            if not handmatig:
                print(f"    -> overgeslagen (geen naam ingevuld).")
                overgeslagen += 1
                continue
            serie_naam = handmatig

        # Bepaal het relatieve pad ten opzichte van de bronmap
        if bron_map:
            try:
                rel_pad = os.path.relpath(bronpad, bron_map)
            except ValueError:
                rel_pad = os.path.basename(bronpad)
        else:
            rel_pad = os.path.basename(bronpad)

        doelpad = os.path.join(output_map, rel_pad)
        os.makedirs(os.path.dirname(doelpad), exist_ok=True)

        try:
            ds = pydicom.dcmread(bronpad, force=True)
        except Exception as e:
            print(f"  ! Kon {bronpad} niet lezen: {e}")
            continue

        # Alleen ProtocolName aanpassen, niets anders
        ds.ProtocolName = _veilige_mapnaam(serie_naam)
        ds.save_as(doelpad)
        totaal_bestanden += 1

    geschreven_series = len({r["serie_uid"] for r in resultaten_per_bestand}) - overgeslagen
    print(f"\nOutput klaar: {geschreven_series} serie(s), {totaal_bestanden} "
          f"bestand(en) geschreven naar: {output_map}")
    if overgeslagen:
        print(f"  ({overgeslagen} bestand(en) overgeslagen.)")

    unieke_series = len({r["serie_uid"] for r in resultaten_per_bestand})
    geschreven_series = unieke_series - overgeslagen
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
            schrijf_naar_output(resultaten, output_map, bron_map=pad,
                                alle_bestanden=bestanden)

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
