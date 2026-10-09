"""
Assemblage MP4 en pur Python (sans FFmpeg, introuvable sur iPhone/Android).

Les sites livrent souvent la vidéo et le son dans deux fichiers séparés
(YouTube, Facebook, Instagram en « DASH »). Ce module les réunit dans un
seul MP4 classique, lisible par Photos, sans réencoder : les données
compressées sont recopiées telles quelles, seul l'index (« moov ») est
reconstruit.

Il accepte des fichiers MP4/M4A classiques ou fragmentés (moof/mdat,
format des flux DASH) et produit toujours un MP4 non fragmenté, avec
l'index au début (lecture immédiate, compatible Photos).

API :
    merge(video_path, audio_path, out_path, progress=None, cancel=None)
    remux(path, out_path, ...)          # un seul fichier (ex. M4A fragmenté)
    is_mp4(path)
"""

import bisect
import os
import struct

__all__ = ["Mp4Error", "Cancelled", "merge", "remux", "is_mp4", "probe"]

MOVIE_TIMESCALE = 1000
COPY_CHUNK = 4 * 1024 * 1024
_U32 = 0xFFFFFFFF


class Mp4Error(Exception):
    pass


class Cancelled(Exception):
    pass


# ----------------------------------------------------------------------
# Lecture des boîtes
# ----------------------------------------------------------------------

def _read_header(handle, position, limit):
    """(type, début, taille_en_tête, fin) d'une boîte, ou None si fin de zone."""
    if position + 8 > limit:
        return None
    handle.seek(position)
    head = handle.read(8)
    if len(head) < 8:
        return None
    size, kind = struct.unpack(">I4s", head)
    header = 8
    if size == 1:
        large = handle.read(8)
        if len(large) < 8:
            raise Mp4Error("Fichier MP4 tronqué.")
        size = struct.unpack(">Q", large)[0]
        header = 16
    elif size == 0:
        size = limit - position
    if size < header:
        raise Mp4Error("Boîte MP4 invalide.")
    end = position + size
    if end > limit:
        # Fichier tronqué (téléchargement interrompu) : on s'arrête là.
        end = limit
    if kind == b"uuid":
        header += 16
    return kind.decode("latin-1"), position, header, end


def _top_level(handle):
    handle.seek(0, os.SEEK_END)
    limit = handle.tell()
    boxes = []
    position = 0
    while True:
        box = _read_header(handle, position, limit)
        if box is None:
            break
        boxes.append(box)
        if box[3] <= position:
            break
        position = box[3]
    return boxes, limit


def _children(data, start=0, end=None):
    """Boîtes contenues dans un bloc d'octets : liste de (type, début, taille_en_tête, fin)."""
    end = len(data) if end is None else end
    position = start
    result = []
    while position + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, position)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, position + 8)[0]
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            break
        result.append((kind.decode("latin-1"), position, header, position + size))
        position += size
    return result


def _find(data, path, start=0, end=None):
    """Première boîte au chemin donné (ex. "mdia/minf/stbl") : (début, taille_en_tête, fin) ou None."""
    current = (start, 0, len(data) if end is None else end)
    for name in path.split("/"):
        found = None
        for kind, box_start, header, box_end in _children(data, current[0] + current[1], current[2]):
            if kind == name:
                found = (box_start, header, box_end)
                break
        if found is None:
            return None
        current = found
    return current


def _find_all(data, name, start, end):
    return [(s, h, e) for kind, s, h, e in _children(data, start, end) if kind == name]


def _raw(data, box):
    return data[box[0]:box[2]] if box else None


def _payload(data, box):
    return data[box[0] + box[1]:box[2]]


def _fullbox(payload):
    version = payload[0]
    flags = int.from_bytes(payload[1:4], "big")
    return version, flags, payload[4:]


# ----------------------------------------------------------------------
# Écriture des boîtes
# ----------------------------------------------------------------------

def _box(kind, *parts):
    body = b"".join(parts)
    size = len(body) + 8
    if size > _U32:
        return struct.pack(">I4sQ", 1, kind.encode("latin-1"), size + 8) + body
    return struct.pack(">I4s", size, kind.encode("latin-1")) + body


def _full(kind, version, flags, *parts):
    return _box(kind, struct.pack(">B", version) + flags.to_bytes(3, "big"), *parts)


# ----------------------------------------------------------------------
# Modèle d'une piste
# ----------------------------------------------------------------------

class Track:
    def __init__(self):
        self.kind = ""          # "vide" ou "soun"
        self.track_id = 0
        self.timescale = 0
        self.language = b"\x55\xc4"  # « und »
        self.tkhd = b""
        self.hdlr = b""
        self.media_header = b""  # vmhd / smhd
        self.stsd = b""
        self.edits = []          # [(durée_en_échelle_du_film_source, media_time, rate)]
        self.source_movie_timescale = MOVIE_TIMESCALE
        self.first_decode_time = 0
        self.offsets = []        # position absolue de chaque échantillon dans le fichier source
        self.sizes = []
        self.durations = []
        self.cts = []
        self.sync = []
        self.sdi = []            # index de description (stsd), 1 en général
        # remplis lors de l'assemblage
        self.source = None

    @property
    def media_duration(self):
        return sum(self.durations)

    def min_presentation_time(self):
        """Plus petit temps d'affichage (dts + décalage) : > 0 quand il y a des images B."""
        if not any(self.cts):
            return 0
        dts = 0
        smallest = None
        for duration, offset in zip(self.durations, self.cts):
            value = dts + offset
            if smallest is None or value < smallest:
                smallest = value
            dts += duration
        return smallest or 0

    def check(self):
        count = len(self.sizes)
        if not count:
            raise Mp4Error("Piste vide.")
        for name in ("offsets", "durations", "cts", "sync", "sdi"):
            if len(getattr(self, name)) != count:
                raise Mp4Error(f"Index MP4 incohérent ({name}).")
        if not self.timescale:
            raise Mp4Error("Échelle de temps absente.")


def _parse_tkhd(payload):
    version, flags, rest = _fullbox(payload)
    if version == 1:
        track_id = struct.unpack_from(">I", rest, 16)[0]
    else:
        track_id = struct.unpack_from(">I", rest, 8)[0]
    return track_id


def _tkhd_geometry(payload):
    """Matrice (rotation) et dimensions d'origine, à conserver."""
    version, _flags, rest = _fullbox(payload)
    offset = 32 if version == 1 else 20
    # reserved(8) layer(2) alternate(2) volume(2) reserved(2)
    offset += 8 + 2 + 2 + 2 + 2
    matrix = rest[offset:offset + 36]
    width, height = struct.unpack_from(">II", rest, offset + 36)
    if len(matrix) != 36:
        matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    return matrix, width, height


def _parse_mdhd(payload):
    version, _flags, rest = _fullbox(payload)
    if version == 1:
        timescale, duration = struct.unpack_from(">IQ", rest, 16)
        language = rest[28:30]
    else:
        timescale, duration = struct.unpack_from(">II", rest, 8)
        language = rest[16:18]
    return timescale, duration, language


def _parse_elst(payload):
    version, _flags, rest = _fullbox(payload)
    count = struct.unpack_from(">I", rest, 0)[0]
    edits = []
    position = 4
    for _ in range(count):
        if version == 1:
            duration, media_time = struct.unpack_from(">Qq", rest, position)
            position += 16
        else:
            duration, media_time = struct.unpack_from(">Ii", rest, position)
            position += 8
        rate = struct.unpack_from(">i", rest, position)[0]
        position += 4
        edits.append((duration, media_time, rate))
    return edits


def _expand_runs(payload, signed=False):
    version, _flags, rest = _fullbox(payload)
    count = struct.unpack_from(">I", rest, 0)[0]
    values = []
    fmt = ">Ii" if (signed and version == 1) else ">II"
    for index in range(count):
        run, value = struct.unpack_from(fmt, rest, 4 + index * 8)
        if signed and version == 0 and value >= 0x80000000:
            value -= 0x100000000  # certains fichiers v0 contiennent des décalages négatifs
        values.extend([value] * run)
    return values


def _parse_stbl(data, stbl, track):
    start = stbl[0] + stbl[1]
    end = stbl[2]
    boxes = {kind: (s, h, e) for kind, s, h, e in _children(data, start, end)}

    stsd = boxes.get("stsd")
    if not stsd:
        raise Mp4Error("Description des échantillons absente.")
    track.stsd = _raw(data, stsd)

    # Tailles
    sizes = []
    if "stsz" in boxes:
        _v, _f, rest = _fullbox(_payload(data, boxes["stsz"]))
        constant, count = struct.unpack_from(">II", rest, 0)
        if constant:
            sizes = [constant] * count
        else:
            sizes = list(struct.unpack_from(f">{count}I", rest, 8))
    elif "stz2" in boxes:
        _v, _f, rest = _fullbox(_payload(data, boxes["stz2"]))
        field = rest[3]
        count = struct.unpack_from(">I", rest, 4)[0]
        table = rest[8:]
        if field == 16:
            sizes = list(struct.unpack_from(f">{count}H", table, 0))
        elif field == 8:
            sizes = list(table[:count])
        else:  # 4 bits
            for index in range(count):
                byte = table[index // 2]
                sizes.append(byte >> 4 if index % 2 == 0 else byte & 0x0F)
    count = len(sizes)
    if count == 0:
        return  # piste « vide » (fichier fragmenté : les échantillons sont dans les moof)

    # Positions des blocs
    if "stco" in boxes:
        _v, _f, rest = _fullbox(_payload(data, boxes["stco"]))
        n = struct.unpack_from(">I", rest, 0)[0]
        chunks = list(struct.unpack_from(f">{n}I", rest, 4))
    elif "co64" in boxes:
        _v, _f, rest = _fullbox(_payload(data, boxes["co64"]))
        n = struct.unpack_from(">I", rest, 0)[0]
        chunks = list(struct.unpack_from(f">{n}Q", rest, 4))
    else:
        raise Mp4Error("Table des positions absente.")

    _v, _f, rest = _fullbox(_payload(data, boxes["stsc"]))
    n = struct.unpack_from(">I", rest, 0)[0]
    stsc = [struct.unpack_from(">III", rest, 4 + i * 12) for i in range(n)]

    offsets = []
    sdis = []
    sample = 0
    for entry_index, (first_chunk, per_chunk, sdi) in enumerate(stsc):
        last_chunk = stsc[entry_index + 1][0] - 1 if entry_index + 1 < len(stsc) else len(chunks)
        for chunk_number in range(first_chunk, last_chunk + 1):
            if chunk_number - 1 >= len(chunks):
                break
            position = chunks[chunk_number - 1]
            for _ in range(per_chunk):
                if sample >= count:
                    break
                offsets.append(position)
                sdis.append(sdi)
                position += sizes[sample]
                sample += 1
    if len(offsets) != count:
        raise Mp4Error("Table des blocs incohérente.")

    durations = _expand_runs(_payload(data, boxes["stts"]))[:count]
    if len(durations) < count:
        durations += [durations[-1] if durations else 1] * (count - len(durations))

    if "ctts" in boxes:
        cts = _expand_runs(_payload(data, boxes["ctts"]), signed=True)[:count]
        cts += [0] * (count - len(cts))
    else:
        cts = [0] * count

    if "stss" in boxes:
        _v, _f, rest = _fullbox(_payload(data, boxes["stss"]))
        n = struct.unpack_from(">I", rest, 0)[0]
        sync_numbers = set(struct.unpack_from(f">{n}I", rest, 4))
        sync = [(index + 1) in sync_numbers for index in range(count)]
    else:
        sync = [True] * count

    track.offsets = offsets
    track.sizes = sizes
    track.durations = durations
    track.cts = cts
    track.sync = sync
    track.sdi = sdis


def _parse_moov(data):
    mvhd = _find(data, "mvhd")
    movie_timescale = MOVIE_TIMESCALE
    if mvhd:
        version, _flags, rest = _fullbox(_payload(data, mvhd))
        movie_timescale = struct.unpack_from(">I", rest, 16 if version == 1 else 8)[0] or MOVIE_TIMESCALE

    tracks = []
    for trak in _find_all(data, "trak", 0, len(data)):
        start, header, end = trak
        start += header  # on cherche à l'intérieur de la boîte trak
        track = Track()
        track.source_movie_timescale = movie_timescale
        tkhd = _find(data, "tkhd", start, end)
        hdlr = _find(data, "mdia/hdlr", start, end)
        mdhd = _find(data, "mdia/mdhd", start, end)
        stbl = _find(data, "mdia/minf/stbl", start, end)
        if not (tkhd and hdlr and mdhd and stbl):
            continue
        track.tkhd = _payload(data, tkhd)
        track.track_id = _parse_tkhd(track.tkhd)
        track.hdlr = _raw(data, hdlr)
        track.kind = _payload(data, hdlr)[8:12].decode("latin-1")
        track.timescale, _duration, track.language = _parse_mdhd(_payload(data, mdhd))
        for name in ("vmhd", "smhd"):
            media_header = _find(data, "mdia/minf/" + name, start, end)
            if media_header:
                track.media_header = _raw(data, media_header)
        elst = _find(data, "edts/elst", start, end)
        if elst:
            track.edits = _parse_elst(_payload(data, elst))
        _parse_stbl(data, stbl, track)
        tracks.append(track)

    trex = {}
    mvex = _find(data, "mvex")
    if mvex:
        for box in _find_all(data, "trex", mvex[0] + mvex[1], mvex[2]):
            _v, _f, rest = _fullbox(_payload(data, box))
            track_id, sdi, duration, size, flags = struct.unpack_from(">5I", rest, 0)
            trex[track_id] = {"sdi": sdi, "duration": duration, "size": size, "flags": flags}
    return tracks, trex


def _is_sync(flags):
    return not (flags & 0x00010000)


def _parse_moof(data, moof_start, tracks_by_id, trex, state):
    """Ajoute aux pistes les échantillons d'un fragment (moof)."""
    previous_end = moof_start  # base implicite du 1er traf
    for traf in _find_all(data, "traf", 8, len(data)):
        t_start, t_header, t_end = traf
        tfhd = _find(data, "tfhd", t_start + t_header, t_end)
        if not tfhd:
            continue
        _version, flags, rest = _fullbox(_payload(data, tfhd))
        track_id = struct.unpack_from(">I", rest, 0)[0]
        position = 4
        defaults = dict(trex.get(track_id) or {"sdi": 1, "duration": 0, "size": 0, "flags": 0})
        if flags & 0x01:
            base = struct.unpack_from(">Q", rest, position)[0]
            position += 8
        elif flags & 0x020000:
            base = moof_start
        else:
            base = previous_end
        if flags & 0x02:
            defaults["sdi"] = struct.unpack_from(">I", rest, position)[0]
            position += 4
        if flags & 0x08:
            defaults["duration"] = struct.unpack_from(">I", rest, position)[0]
            position += 4
        if flags & 0x10:
            defaults["size"] = struct.unpack_from(">I", rest, position)[0]
            position += 4
        if flags & 0x20:
            defaults["flags"] = struct.unpack_from(">I", rest, position)[0]
            position += 4

        track = tracks_by_id.get(track_id)

        tfdt = _find(data, "tfdt", t_start + t_header, t_end)
        if tfdt and track is not None and not state.setdefault(track_id, {}).get("seen"):
            version, _f, tfdt_rest = _fullbox(_payload(data, tfdt))
            track.first_decode_time = struct.unpack_from(">Q" if version == 1 else ">I", tfdt_rest, 0)[0]
        state.setdefault(track_id, {})["seen"] = True

        next_data = base
        for trun in _find_all(data, "trun", t_start + t_header, t_end):
            version, trun_flags, rest = _fullbox(_payload(data, trun))
            count = struct.unpack_from(">I", rest, 0)[0]
            position = 4
            if trun_flags & 0x01:
                data_offset = struct.unpack_from(">i", rest, position)[0]
                position += 4
                cursor = base + data_offset
            else:
                cursor = next_data
            first_flags = None
            if trun_flags & 0x04:
                first_flags = struct.unpack_from(">I", rest, position)[0]
                position += 4
            for index in range(count):
                duration = defaults["duration"]
                size = defaults["size"]
                sample_flags = defaults["flags"]
                cto = 0
                if trun_flags & 0x100:
                    duration = struct.unpack_from(">I", rest, position)[0]
                    position += 4
                if trun_flags & 0x200:
                    size = struct.unpack_from(">I", rest, position)[0]
                    position += 4
                if trun_flags & 0x400:
                    sample_flags = struct.unpack_from(">I", rest, position)[0]
                    position += 4
                elif index == 0 and first_flags is not None:
                    sample_flags = first_flags
                if trun_flags & 0x800:
                    cto = struct.unpack_from(">i" if version == 1 else ">I", rest, position)[0]
                    position += 4
                if track is not None:
                    track.offsets.append(cursor)
                    track.sizes.append(size)
                    track.durations.append(duration)
                    track.cts.append(cto)
                    track.sync.append(_is_sync(sample_flags) if track.kind == "vide" else True)
                    track.sdi.append(defaults["sdi"] or 1)
                cursor += size
            next_data = cursor
        previous_end = next_data


class Source:
    """Un fichier d'entrée : pistes + zones de données (mdat) à recopier."""

    def __init__(self, path):
        self.path = path
        self.mdats = []   # [(début, fin)] boîtes mdat complètes (en-tête compris)
        self.tracks = []
        self.size = 0
        try:
            self._parse()
        except (struct.error, IndexError, ValueError, OverflowError) as error:
            raise Mp4Error(f"Fichier MP4 illisible ou incomplet ({error}).") from error
        for track in self.tracks:
            track.source = self

    def _parse(self):
        path = self.path
        with open(path, "rb") as handle:
            boxes, self.size = _top_level(handle)
            kinds = [box[0] for box in boxes]
            if "moov" not in kinds:
                if kinds[:1] == ["ftyp"] or "moof" in kinds:
                    raise Mp4Error("Fichier MP4 incomplet (index absent).")
                raise Mp4Error("Ce fichier n'est pas au format MP4.")
            moov = next(box for box in boxes if box[0] == "moov")
            handle.seek(moov[1] + moov[2])
            moov_data = handle.read(moov[3] - moov[1] - moov[2])  # contenu du moov, sans en-tête
            self.tracks, trex = _parse_moov(moov_data)
            by_id = {track.track_id: track for track in self.tracks}
            state = {}
            for kind, start, header, end in boxes:
                if kind == "mdat":
                    self.mdats.append((start, end))
                elif kind == "moof":
                    handle.seek(start)
                    moof = handle.read(end - start)
                    _parse_moof(moof, start, by_id, trex, state)

    def track(self, kind):
        for track in self.tracks:
            if track.kind == kind and track.sizes:
                return track
        return None


# ----------------------------------------------------------------------
# Construction du fichier de sortie
# ----------------------------------------------------------------------

def _runs(values):
    runs = []
    for value in values:
        if runs and runs[-1][1] == value:
            runs[-1][0] += 1
        else:
            runs.append([1, value])
    return runs


def _ftyp(audio_only):
    if audio_only:
        return _box("ftyp", b"M4A ", struct.pack(">I", 0x200), b"M4A ", b"isom", b"iso2", b"mp42")
    return _box("ftyp", b"isom", struct.pack(">I", 0x200), b"isom", b"iso2", b"avc1", b"mp41")


def _movie_duration(media_duration, timescale):
    return (media_duration * MOVIE_TIMESCALE + timescale - 1) // timescale


def _edits_for(track, movie_duration):
    """Liste d'éditions de la piste, convertie à l'échelle de temps du film de sortie."""
    edits = []
    for duration, media_time, rate in track.edits:
        if duration == 0:
            duration_out = movie_duration
        else:
            duration_out = (duration * MOVIE_TIMESCALE + track.source_movie_timescale - 1) // track.source_movie_timescale
        edits.append((duration_out, media_time, rate))
    if not edits:
        # Fichier fragmenté (DASH) : pas de liste d'éditions. On en crée une
        # pour que l'image 0 s'affiche au temps 0 (décalage des images B) et
        # pour respecter un éventuel départ retardé de la piste (tfdt).
        delay = (track.first_decode_time * MOVIE_TIMESCALE) // track.timescale
        start = track.min_presentation_time()
        if delay > 0:
            edits.append((delay, -1, 0x10000))
        if delay > 0 or start > 0:
            edits.append((movie_duration, max(0, start), 0x10000))
    return edits


def _elst(edits):
    large = any(d > _U32 or m > 0x7FFFFFFF or m < -0x80000000 for d, m, _r in edits)
    body = struct.pack(">I", len(edits))
    for duration, media_time, rate in edits:
        if large:
            body += struct.pack(">Qqi", duration, media_time, rate)
        else:
            body += struct.pack(">Iii", duration, media_time, rate)
    return _box("edts", _full("elst", 1 if large else 0, 0, body))


def _tkhd(track, new_id, duration):
    matrix, width, height = _tkhd_geometry(track.tkhd)
    volume = 0x0100 if track.kind == "soun" else 0
    if duration > _U32:
        head = struct.pack(">QQIIQ", 0, 0, new_id, 0, duration)
        version = 1
    else:
        head = struct.pack(">IIIII", 0, 0, new_id, 0, duration)
        version = 0
    tail = struct.pack(">8xhhH2x", 0, 0, volume) + matrix + struct.pack(">II", width, height)
    return _full("tkhd", version, 0x000003, head, tail)


def _mdhd(track):
    duration = track.media_duration
    if duration > _U32:
        return _full("mdhd", 1, 0, struct.pack(">QQIQ", 0, 0, track.timescale, duration), track.language, b"\x00\x00")
    return _full("mdhd", 0, 0, struct.pack(">IIII", 0, 0, track.timescale, duration), track.language, b"\x00\x00")


def _dinf():
    return _box("dinf", _full("dref", 0, 0, struct.pack(">I", 1), _full("url ", 0, 1)))


def _normalize_stsd(stsd):
    """Les vidéos HEVC marquées « hev1 » ne se lisent pas sur iPhone (AVFoundation
    exige « hvc1 ») : on corrige l'étiquette, les paramètres restent dans hvcC."""
    if not stsd or len(stsd) < 24:
        return stsd
    data = bytearray(stsd)
    count = struct.unpack(">I", bytes(data[12:16]))[0]
    position = 16
    for _ in range(count):
        if position + 8 > len(data):
            break
        size = struct.unpack(">I", bytes(data[position:position + 4]))[0]
        if bytes(data[position + 4:position + 8]) == b"hev1":
            data[position + 4:position + 8] = b"hvc1"
        if size < 8:
            break
        position += size
    return bytes(data)


def _stbl(track, chunk_offsets, chunk_counts, chunk_sdis, use_co64):
    count = len(track.sizes)
    parts = [_normalize_stsd(track.stsd)]

    stts = _runs(track.durations)
    parts.append(_full("stts", 0, 0, struct.pack(">I", len(stts)),
                       b"".join(struct.pack(">II", n, v) for n, v in stts)))

    if any(track.cts):
        negative = any(value < 0 for value in track.cts)
        ctts = _runs(track.cts)
        fmt = ">Ii" if negative else ">II"
        parts.append(_full("ctts", 1 if negative else 0, 0, struct.pack(">I", len(ctts)),
                           b"".join(struct.pack(fmt, n, v) for n, v in ctts)))

    if not all(track.sync):
        numbers = [index + 1 for index, flag in enumerate(track.sync) if flag]
        parts.append(_full("stss", 0, 0, struct.pack(">I", len(numbers)),
                           struct.pack(f">{len(numbers)}I", *numbers)))

    entries = []
    for index, (samples, sdi) in enumerate(zip(chunk_counts, chunk_sdis)):
        if entries and entries[-1][1] == samples and entries[-1][2] == sdi:
            continue
        entries.append((index + 1, samples, sdi))
    parts.append(_full("stsc", 0, 0, struct.pack(">I", len(entries)),
                       b"".join(struct.pack(">III", *entry) for entry in entries)))

    if len(set(track.sizes)) == 1:
        parts.append(_full("stsz", 0, 0, struct.pack(">II", track.sizes[0], count)))
    else:
        parts.append(_full("stsz", 0, 0, struct.pack(">II", 0, count), struct.pack(f">{count}I", *track.sizes)))

    if use_co64:
        parts.append(_full("co64", 0, 0, struct.pack(">I", len(chunk_offsets)),
                           struct.pack(f">{len(chunk_offsets)}Q", *chunk_offsets)))
    else:
        parts.append(_full("stco", 0, 0, struct.pack(">I", len(chunk_offsets)),
                           struct.pack(f">{len(chunk_offsets)}I", *chunk_offsets)))
    return _box("stbl", *parts)


def _chunks(track, relocate):
    """Regroupe les échantillons contigus en blocs ; renvoie (positions, nb_par_bloc, sdi_par_bloc)."""
    offsets, counts, sdis = [], [], []
    expected = None
    for offset, size, sdi in zip(track.offsets, track.sizes, track.sdi):
        new_offset = relocate(track.source, offset, size)
        if expected is not None and new_offset == expected and sdis[-1] == sdi and counts[-1] < 4096:
            counts[-1] += 1
        else:
            offsets.append(new_offset)
            counts.append(1)
            sdis.append(sdi)
        expected = new_offset + size
    return offsets, counts, sdis


def _moov(tracks, relocate, use_co64):
    traks = []
    movie_duration = 0
    for new_id, track in enumerate(tracks, start=1):
        duration = _movie_duration(track.media_duration, track.timescale)
        edits = _edits_for(track, duration)
        if edits:
            duration = max(duration, sum(d for d, _m, _r in edits))
        movie_duration = max(movie_duration, duration)
        offsets, counts, sdis = _chunks(track, relocate)
        minf = _box("minf", track.media_header or b"", _dinf(), _stbl(track, offsets, counts, sdis, use_co64))
        mdia = _box("mdia", _mdhd(track), track.hdlr, minf)
        traks.append(_box("trak", _tkhd(track, new_id, duration), _elst(edits) if edits else b"", mdia))

    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    if movie_duration > _U32:
        head = struct.pack(">QQIQ", 0, 0, MOVIE_TIMESCALE, movie_duration)
        version = 1
    else:
        head = struct.pack(">IIII", 0, 0, MOVIE_TIMESCALE, movie_duration)
        version = 0
    mvhd = _full("mvhd", version, 0, head, struct.pack(">iH10x", 0x10000, 0x0100), matrix,
                 b"\x00" * 24, struct.pack(">I", len(tracks) + 1))
    return _box("moov", mvhd, *traks)


def _write(tracks, out_path, progress=None, cancel=None):
    for track in tracks:
        track.check()

    sources = []
    for track in tracks:
        if track.source not in sources:
            sources.append(track.source)

    # Seules les boîtes mdat utiles sont recopiées.
    used = []
    for source in sources:
        for start, end in source.mdats:
            used.append((source, start, end))
    if not used:
        raise Mp4Error("Aucune donnée audio/vidéo trouvée.")

    total_data = sum(end - start for _s, start, end in used)
    use_co64 = total_data > _U32 - 64 * 1024 * 1024
    audio_only = all(track.kind == "soun" for track in tracks)
    ftyp = _ftyp(audio_only)

    def build(base):
        positions = {}
        cursor = base
        for source, start, end in used:
            positions[(id(source), start)] = cursor
            cursor += end - start

        starts = {id(source): [start for start, _end in source.mdats] for source in sources}

        def relocate(source, offset, size):
            index = bisect.bisect_right(starts[id(source)], offset) - 1
            if index >= 0:
                mdat_start, mdat_end = source.mdats[index]
                if offset + size <= mdat_end:
                    return positions[(id(source), mdat_start)] + (offset - mdat_start)
            raise Mp4Error("Échantillon hors des données du fichier.")

        return _moov(tracks, relocate, use_co64)

    # La taille du moov ne dépend pas des valeurs des positions : deux passes suffisent.
    first = build(len(ftyp) + 0)
    moov = build(len(ftyp) + len(first))
    if len(moov) != len(first):
        raise Mp4Error("Index MP4 instable.")

    temp_path = out_path + ".playermux"
    copied = 0
    try:
        with open(temp_path, "wb") as out:
            out.write(ftyp)
            out.write(moov)
            for source, start, end in used:
                with open(source.path, "rb") as handle:
                    handle.seek(start)
                    remaining = end - start
                    while remaining > 0:
                        if cancel is not None and cancel():
                            raise Cancelled()
                        block = handle.read(min(COPY_CHUNK, remaining))
                        if not block:
                            raise Mp4Error("Fichier source tronqué pendant l'assemblage.")
                        out.write(block)
                        remaining -= len(block)
                        copied += len(block)
                        if progress is not None:
                            progress(copied / total_data)
        os.replace(temp_path, out_path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    return out_path


# ----------------------------------------------------------------------
# API publique
# ----------------------------------------------------------------------

def is_mp4(path):
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] in (b"ftyp", b"moov", b"styp", b"free", b"mdat", b"skip", b"wide")


def probe(path):
    """Résumé d'un fichier : {"video": bool, "audio": bool, "fragmented": bool}."""
    source = Source(path)
    with open(path, "rb") as handle:
        kinds = [box[0] for box in _top_level(handle)[0]]
    return {
        "video": source.track("vide") is not None,
        "audio": source.track("soun") is not None,
        "fragmented": "moof" in kinds,
    }


def merge(video_path, audio_path, out_path, progress=None, cancel=None):
    """Réunit la piste vidéo de video_path et la piste audio de audio_path dans out_path."""
    tracks = []
    if video_path:
        video = Source(video_path).track("vide")
        if video is None:
            raise Mp4Error("Aucune piste vidéo dans le fichier vidéo.")
        tracks.append(video)
    if audio_path:
        audio = Source(audio_path).track("soun")
        if audio is None:
            raise Mp4Error("Aucune piste audio dans le fichier audio.")
        tracks.append(audio)
    if not tracks:
        raise Mp4Error("Rien à assembler.")
    return _write(tracks, out_path, progress, cancel)


def remux(path, out_path, kinds=("vide", "soun"), progress=None, cancel=None):
    """Réécrit un fichier (ex. fragmenté) en MP4/M4A classique avec les pistes demandées."""
    source = Source(path)
    tracks = [track for kind in kinds for track in [source.track(kind)] if track is not None]
    if not tracks:
        raise Mp4Error("Aucune piste audio ou vidéo exploitable.")
    return _write(tracks, out_path, progress, cancel)
