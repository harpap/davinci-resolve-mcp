"""sanitize_media / relink_media / import_source_clips="false" must stay false.

timeline import_timeline_checked read these with bare bool(): a string
"false", "no", "0" or "off" is truthy, so a caller opting out of the sanitize
pass still had the XML rewritten (clips with missing media dropped from the
import), and a caller opting out of source-clip import still sent
importSourceClips=True to Resolve.
"""
import os
import tempfile
import unittest
import urllib.parse
from unittest import mock

import src.server as s

FALSE_SPELLINGS = ("false", "False", "no", "0", "off")


class _Timeline:
    def __init__(self, tid):
        self._id = tid

    def GetUniqueId(self):
        return self._id

    def GetName(self):
        return "Imported"


class _Project:
    def __init__(self):
        self.timelines = []

    def GetName(self):
        return "A Named Project"

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]


class _MediaPool:
    def __init__(self, proj):
        self.proj = proj
        self.calls = []

    def ImportTimelineFromFile(self, path, options):
        self.calls.append((path, dict(options)))
        tl = _Timeline(f"tl-{len(self.calls)}")
        self.proj.timelines.append(tl)
        return tl


def _xmeml(missing_path):
    url = "file://localhost" + urllib.parse.quote(missing_path)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
<sequence id="seq1">
<name>SEQ</name>
<rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>
<media><video><track>
<clipitem id="ci1"><name>offline.mov</name><start>0</start><end>100</end><in>0</in><out>100</out>
<file id="f1"><name>offline.mov</name><pathurl>{url}</pathurl><duration>500</duration></file></clipitem>
</track></video></media>
</sequence>
</xmeml>
"""


class ImportTimelineCheckedCoercionTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_xmeml(os.path.join(tempfile.gettempdir(), "no-such-dir", "offline.mov")))
        self.addCleanup(os.unlink, self.path)

    def _import(self, params):
        proj = _Project()
        mp = _MediaPool(proj)
        with mock.patch.object(s, "_check", return_value=(None, proj, None)), \
             mock.patch.object(s, "_get_mp", return_value=(None, proj, mp, None)):
            out = s.timeline("import_timeline_checked", dict(params, path=self.path))
        self.assertTrue(out.get("success"), out)
        self.assertEqual(len(mp.calls), 1)
        return out, mp.calls[0]

    def test_sanitize_media_false_spellings_import_the_file_as_is(self):
        for key in ("sanitize_media", "relink_media"):
            for spelling in FALSE_SPELLINGS:
                with self.subTest(key=key, spelling=spelling):
                    out, (import_path, _options) = self._import({key: spelling})
                    self.assertEqual(import_path, self.path)
                    self.assertNotIn("sanitize", out)

    def test_sanitize_media_true_still_sanitizes(self):
        out, (import_path, _options) = self._import({"sanitize_media": "true"})
        self.addCleanup(lambda: os.path.exists(import_path) and os.unlink(import_path))
        self.assertNotEqual(import_path, self.path)
        self.assertEqual(out["sanitize"]["missing_media_count"], 1)

    def test_import_source_clips_false_spellings_reach_resolve_as_false(self):
        for spelling in FALSE_SPELLINGS:
            with self.subTest(spelling=spelling):
                _out, (_path, options) = self._import({"import_source_clips": spelling})
                self.assertIs(options["importSourceClips"], False)

    def test_import_source_clips_true_reaches_resolve_as_true(self):
        _out, (_path, options) = self._import({"import_source_clips": True})
        self.assertIs(options["importSourceClips"], True)


if __name__ == "__main__":
    unittest.main()
