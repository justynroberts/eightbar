"""Measure audio properly, since Live will not tell us anything.

Live's Spectrum and Tuner expose exactly one parameter -- Device On. Their
analysis is drawn to the screen and never published, and third-party meters are
worse: a plugin exposes no parameters at all until Configure is pressed on it.
So no analyser you can install makes a mix measurable from here.

What does work is measuring the audio itself. Live can resample its own master
into an audio track without any manual export, and that file can be analysed
for the things that actually decide a mix: where the energy sits across the
spectrum, how loud it really is, how much dynamic range is left, and whether
the low end is mono.

None of this replaces listening. It catches the things ears are bad at --
a 3dB bump at 200Hz, a sub that is 6dB louder than the reference, a limiter
already doing 4dB of work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile

# Bands chosen to match how producers actually talk about a mix, rather than
# octave-spaced academic bands.
BANDS: tuple[tuple[str, float, float], ...] = (
    ("sub", 20, 60),
    ("bass", 60, 120),
    ("low_mid", 120, 350),
    ("mid", 350, 2000),
    ("high_mid", 2000, 6000),
    ("high", 6000, 12000),
    ("air", 12000, 20000),
)

# Typical energy split for a finished EDM master, as a share of total power.
# Wide tolerances: this flags a problem, it does not prescribe a sound.
EDM_TARGETS: dict[str, tuple[float, float]] = {
    "sub": (0.08, 0.28),
    "bass": (0.14, 0.34),
    "low_mid": (0.10, 0.26),
    "mid": (0.12, 0.30),
    "high_mid": (0.04, 0.16),
    "high": (0.01, 0.08),
    "air": (0.001, 0.03),
}


@dataclass
class Analysis:
    path: str
    seconds: float
    sample_rate: int
    channels: int
    peak_db: float
    true_peak_db: float
    rms_db: float
    lufs: float
    crest_db: float
    bands: dict[str, float] = field(default_factory=dict)
    band_db: dict[str, float] = field(default_factory=dict)
    stereo_width: float = 0.0
    bass_mono: float = 1.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "file": self.path,
            "seconds": round(self.seconds, 2),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "peak_db": round(self.peak_db, 2),
            "true_peak_db": round(self.true_peak_db, 2),
            "rms_db": round(self.rms_db, 2),
            "lufs": round(self.lufs, 2),
            "crest_db": round(self.crest_db, 2),
            "band_share": {k: round(v, 4) for k, v in self.bands.items()},
            "band_db": {k: round(v, 2) for k, v in self.band_db.items()},
            "stereo_width": round(self.stereo_width, 3),
            "bass_mono": round(self.bass_mono, 3),
            "notes": self.notes,
        }


def _db(value: float, floor: float = -120.0) -> float:
    return max(floor, 20.0 * np.log10(value)) if value > 0 else floor


def analyse(path: str | Path, max_seconds: float = 120.0) -> Analysis:
    """Measure one audio file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no audio file at {path}")

    data, rate = soundfile.read(str(path), always_2d=True, dtype="float64")
    if data.shape[0] > int(max_seconds * rate):
        data = data[: int(max_seconds * rate)]
    channels = data.shape[1]
    mono = data.mean(axis=1)

    peak = float(np.max(np.abs(data))) if data.size else 0.0
    rms = float(np.sqrt(np.mean(mono**2))) if mono.size else 0.0

    # True peak needs oversampling; 4x catches most inter-sample overs.
    upsampled = np.interp(
        np.linspace(0, len(mono), len(mono) * 4, endpoint=False),
        np.arange(len(mono)),
        mono,
    )
    true_peak = float(np.max(np.abs(upsampled))) if upsampled.size else 0.0

    try:
        meter = pyloudnorm.Meter(rate)
        lufs = float(meter.integrated_loudness(data if channels > 1 else mono))
    except Exception:
        # Too short to measure (the meter needs ~0.4s).
        lufs = float("nan")

    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1.0 / rate)
    total = float(spectrum.sum()) or 1.0

    bands, band_db = {}, {}
    for name, low, high in BANDS:
        mask = (freqs >= low) & (freqs < high)
        energy = float(spectrum[mask].sum())
        bands[name] = energy / total
        band_db[name] = _db(np.sqrt(energy / max(1, mask.sum())))

    width, bass_mono = 0.0, 1.0
    if channels >= 2:
        left, right = data[:, 0], data[:, 1]
        side = (left - right) / 2.0
        mid = (left + right) / 2.0
        mid_power = float(np.mean(mid**2)) or 1e-12
        width = float(np.mean(side**2) / mid_power)

        # Correlation below 120Hz: 1.0 is fully mono, 0 or less is trouble.
        low_freqs = freqs < 120
        if low_freqs.any():
            lo_l = np.fft.irfft(np.fft.rfft(left) * low_freqs)
            lo_r = np.fft.irfft(np.fft.rfft(right) * low_freqs)
            denom = float(np.std(lo_l) * np.std(lo_r))
            bass_mono = float(np.corrcoef(lo_l, lo_r)[0, 1]) if denom > 1e-12 else 1.0

    result = Analysis(
        path=str(path),
        seconds=len(mono) / rate,
        sample_rate=rate,
        channels=channels,
        peak_db=_db(peak),
        true_peak_db=_db(true_peak),
        rms_db=_db(rms),
        lufs=lufs,
        crest_db=_db(peak) - _db(rms),
        bands=bands,
        band_db=band_db,
        stereo_width=width,
        bass_mono=bass_mono,
    )
    result.notes = observations(result)
    return result


# Below this the file is silence, and every other measurement is meaningless.
SILENCE_DB = -70.0


def observations(a: Analysis) -> list[str]:
    """Turn the numbers into things worth doing something about."""
    notes: list[str] = []

    # Silence must be reported as silence. Describing an empty recording as
    # "heavily limited" and "thin across every band" is worse than useless.
    if a.peak_db <= SILENCE_DB:
        return [
            f"This file is silent (peak {a.peak_db:.0f}dB). Nothing was playing "
            "when it was captured -- check the transport was running, a scene "
            "was fired, and the tracks were not muted.",
        ]

    if a.true_peak_db > -0.1:
        notes.append(
            f"True peak {a.true_peak_db:+.1f}dBTP is clipping. Pull the master "
            "down or set a limiter ceiling at -0.3dB."
        )
    elif a.true_peak_db > -0.3:
        notes.append(f"True peak {a.true_peak_db:+.1f}dBTP leaves no headroom for encoding.")

    if not np.isnan(a.lufs) and np.isfinite(a.lufs):
        # Club and EDM masters sit around -9 to -7 LUFS. Streaming normalises
        # to about -14, so anything louder is turned down on playback -- the
        # dynamics are given away without any gain in perceived loudness.
        if a.lufs > -5:
            notes.append(
                f"{a.lufs:.1f} LUFS is past the point of any benefit. Streaming "
                "normalises to about -14, so this is turned down on playback "
                "and you have spent the dynamics for nothing."
            )
        elif a.lufs > -6.5:
            notes.append(
                f"{a.lufs:.1f} LUFS is at the loud end even for club playback "
                "(-9 to -7 is the usual range)."
            )
        elif a.lufs < -13:
            notes.append(
                f"{a.lufs:.1f} LUFS is quiet for a club track; -9 to -7 is "
                "typical for EDM, though streaming will normalise it anyway."
            )

    if a.crest_db < 6:
        notes.append(
            f"Crest factor {a.crest_db:.1f}dB is flat -- the mix is heavily limited "
            "and the kick has lost its transient."
        )
    elif a.crest_db > 18:
        notes.append(f"Crest factor {a.crest_db:.1f}dB is very dynamic; peaks are far above the body.")

    for name, (low, high) in EDM_TARGETS.items():
        share = a.bands.get(name, 0.0)
        if share > high:
            notes.append(
                f"{name.replace('_', ' ')} is {share:.0%} of total energy "
                f"(typical {low:.0%}-{high:.0%}) -- crowded."
            )
        elif share < low:
            notes.append(
                f"{name.replace('_', ' ')} is only {share:.0%} "
                f"(typical {low:.0%}-{high:.0%}) -- thin."
            )

    if a.channels >= 2:
        if a.bass_mono < 0.6:
            notes.append(
                f"Low end correlation {a.bass_mono:.2f} -- the bass is not mono and "
                "will lose power on a club system. Put a Utility with Bass Mono on it."
            )
        if a.stereo_width > 0.9:
            notes.append(f"Very wide (side/mid {a.stereo_width:.2f}); check it in mono.")
        elif a.stereo_width < 0.05:
            notes.append(f"Nearly mono (side/mid {a.stereo_width:.2f}).")

    if not notes:
        notes.append("Nothing obviously wrong. The rest is a judgement call.")
    return notes


def compare(mix: Analysis, reference: Analysis) -> dict:
    """Compare a mix against a reference track, band by band.

    The single most useful thing you can do with measurement: not "is this
    correct" but "how does this differ from something that works".
    """
    deltas, advice = {}, []
    for name, _low, _high in BANDS:
        mine = mix.bands.get(name, 0.0)
        theirs = reference.bands.get(name, 0.0)
        if theirs <= 0:
            continue
        ratio = mine / theirs
        delta_db = 10 * np.log10(ratio) if ratio > 0 else -60.0
        deltas[name] = round(float(delta_db), 2)
        if abs(delta_db) >= 2.0:
            direction = "more" if delta_db > 0 else "less"
            advice.append(
                f"{abs(delta_db):.1f}dB {direction} {name.replace('_', ' ')} "
                f"than the reference."
            )

    loudness_gap = (
        round(mix.lufs - reference.lufs, 2)
        if not (np.isnan(mix.lufs) or np.isnan(reference.lufs))
        else None
    )
    return {
        "band_delta_db": deltas,
        "loudness_delta_lufs": loudness_gap,
        "crest_delta_db": round(mix.crest_db - reference.crest_db, 2),
        "width_delta": round(mix.stereo_width - reference.stereo_width, 3),
        "advice": advice or ["Spectral balance is close to the reference."],
    }
