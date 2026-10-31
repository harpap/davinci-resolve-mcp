"""Preservation and failure-atomicity regressions for native ripple insertion."""
import tempfile
from pathlib import Path
import unittest
from unittest import mock
import zipfile

from src.utils import native_ripple as n


def sequence(items, extra=b""):
    return (b'<Sm2SequenceContainer><VideoTrackVec><Element><Sm2TiTrack><Items>'
            + items + b'</Items></Sm2TiTrack></Element></VideoTrackVec>' + extra
            + b'</Sm2SequenceContainer>')


def clip(start=100, duration=40, payload=b"", tag=b"Sm2TiVideoClip"):
    return (b'<Element><' + tag + b' DbId="clip-id"><Start>' + str(start).encode()
            + b'</Start><Duration>' + str(duration).encode() + b'</Duration>' + payload
            + b'</' + tag + b'></Element>')


class NativePatchTests(unittest.TestCase):
    def test_only_record_positions_change_and_opaque_state_is_byte_exact(self):
        opaque = (b'<In>31</In><FieldsBlob>abcdef</FieldsBlob><MediaTimemapBA>0102</MediaTimemapBA>'
                  b'<CompositionBA>0304</CompositionBA><Grade><Start>999</Start></Grade>'
                  b'<LinkedItemSync>audio-id</LinkedItemSync><Name>\xce\xb2 &amp; shot</Name>')
        original = sequence(clip(100, 40, opaque) + clip(140, 30, opaque))
        patched, count = n.shift_sequence(original, 140, 24)
        self.assertEqual(count, 1)
        self.assertEqual(patched, original.replace(b'<Start>140</Start>', b'<Start>164</Start>'))

    def test_audio_and_transitions_move_without_rewriting_effects(self):
        transition = clip(180, 12, b'<Effect>opaque</Effect>', b'Sm2TiTransition')
        audio = b'<AudioTrackVec><Element><Sm2TiTrack><Items>' + clip(140, 40, b'<EffectFiltersBA>deadbeef</EffectFiltersBA>', b'Sm2TiAudioClip') + b'</Items></Sm2TiTrack></Element></AudioTrackVec>'
        original = sequence(clip(140, 40) + transition, audio)
        patched, count = n.shift_sequence(original, 140, 24)
        self.assertEqual(count, 3)
        self.assertEqual(patched, original.replace(b'<Start>140</Start>', b'<Start>164</Start>').replace(b'<Start>180</Start>', b'<Start>204</Start>'))

    def test_transition_crossing_insertion_point_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'crosses'):
            n.shift_sequence(sequence(clip(130, 20, tag=b'Sm2TiTransition')), 140, 24)

    def test_unknown_native_item_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported native timeline item'):
            n.shift_sequence(sequence(clip(tag=b'FutureItem')), 100, 24)

    def test_malformed_native_time_is_refused(self):
        for data in (sequence(clip()).replace(b'<Start>100</Start>', b'<Start/>'),
                     sequence(clip()).replace(b'<Duration>40</Duration>', b'<Duration>nan</Duration>')):
            with self.assertRaises(ValueError):
                n.shift_sequence(data, 100, 24)

    def test_all_other_zip_members_are_unchanged_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'source.drt', Path(directory) / 'output.drt'
            members = {'project.xml': b'project', 'MediaPool/Master/MpFolder.xml': b'media',
                       'SeqContainer/seq.xml': sequence(clip())}
            with zipfile.ZipFile(source, 'w') as z:
                for name, data in members.items():
                    z.writestr(name, data)
            original_bytes = source.read_bytes()
            self.assertEqual(n.shift_archive(source, output, 100, 24), 1)
            with zipfile.ZipFile(output) as z:
                for name in members:
                    if not name.startswith('SeqContainer/'):
                        self.assertEqual(z.read(name), members[name])
            self.assertEqual(source.read_bytes(), original_bytes)
            with self.assertRaises(FileExistsError):
                n.shift_archive(source, output, 100, 24)
            with self.assertRaises(FileExistsError):
                n.shift_archive(source, source, 100, 24)

    def test_nested_sequences_refuse_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'source.drt', Path(directory) / 'output.drt'
            with zipfile.ZipFile(source, 'w') as z:
                z.writestr('project.xml', b'project')
                for name in ('one', 'nested'):
                    z.writestr(f'SeqContainer/{name}.xml', sequence(clip()))
            with self.assertRaisesRegex(ValueError, 'nested timelines'):
                n.shift_archive(source, output, 100, 24)
            self.assertFalse(output.exists())


class RevisionFailureTests(unittest.TestCase):
    """A failed revision must never become the user's active working edit."""
    def run_failure(self, *, export=True, imported=True, changed=False, append=True, restore=True):
        timeline, revision, pool, project, resolve = (mock.Mock() for _ in range(5))
        timeline.GetName.return_value = 'Original'
        timeline.GetUniqueId.return_value = 'original-id'
        timeline.GetMarkers.return_value = {}
        timeline.Export.return_value = export
        project.GetCurrentTimeline.return_value = timeline
        project.GetMediaPool.return_value = pool
        pool.ImportTimelineFromFile.return_value = revision if imported else None
        pool.AppendToTimeline.return_value = [mock.Mock()] if append else None
        revision.SetName.return_value = True
        revision.GetName.return_value = 'Revision'
        revision.GetUniqueId.return_value = 'revision-id'
        revision.GetMarkers.return_value = {}
        before = {'tracks': {'video': 1}, 'items': []}
        after = {'tracks': {'video': 1}, 'items': [{'wrong': True}]} if changed else before
        plan = {'insert_frame_absolute': 100, 'shift_frames': 24, 'tail_item_count': 0,
                'inserts': [], 'gap_frames_by_track': {}, 'warnings': []}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(n, 'inventory', side_effect=[before, after]), mock.patch.object(n, 'shift_archive', return_value=0), mock.patch.object(n, 'set_current_timeline', return_value=(restore, {})) as switch:
            result = n.execute(resolve, project, timeline, [{}], plan, lambda _: directory, 'Revision')
        self.assertFalse(result['success'])
        self.assertEqual(result['original_restored'], restore)
        switch.assert_called_with(project, timeline)
        timeline.DeleteClips.assert_not_called()
        pool.DeleteTimelines.assert_not_called()
        return result, pool

    def test_export_failure_leaves_original_active_and_does_not_import(self):
        _, pool = self.run_failure(export=False)
        pool.ImportTimelineFromFile.assert_not_called()

    def test_import_failure_leaves_original_active(self):
        _, pool = self.run_failure(imported=False)
        pool.AppendToTimeline.assert_not_called()

    def test_lost_state_is_rejected_before_appending(self):
        result, pool = self.run_failure(changed=True)
        self.assertIn('preserve the edit', result['error']['message'])
        pool.AppendToTimeline.assert_not_called()

    def test_append_failure_reselects_original(self):
        result, _ = self.run_failure(append=False)
        self.assertIn('AppendToTimeline failed', result['error']['message'])

    def test_restore_failure_is_reported_honestly(self):
        result, _ = self.run_failure(export=False, restore=False)
        self.assertIn('restore_error', result)


if __name__ == '__main__':
    unittest.main()
