from scripts.production.providers.voice import WordTiming
from scripts.production.subtitles import group_words_into_cues, write_srt


def _word(text, start, end):
    return WordTiming(text=text, start=start, end=end)


def test_groups_words_by_max_word_count():
    words = [_word(f"w{i}", i * 0.3, i * 0.3 + 0.3) for i in range(12)]
    cues = group_words_into_cues(words, max_words=6, max_seconds=999)
    assert len(cues) == 2
    assert cues[0].text == "w0 w1 w2 w3 w4 w5"
    assert cues[1].text == "w6 w7 w8 w9 w10 w11"


def test_groups_words_by_max_duration():
    words = [_word(f"w{i}", i * 1.0, i * 1.0 + 1.0) for i in range(6)]
    cues = group_words_into_cues(words, max_words=99, max_seconds=2.5)
    # Each cue's span (end - start) must never exceed max_seconds.
    for cue in cues:
        assert cue.end - cue.start <= 2.5 + 1e-9


def test_breaks_early_on_sentence_end_punctuation():
    words = [_word("Hello,", 0.0, 0.5), _word("world.", 0.5, 1.0), _word("Next", 1.0, 1.5), _word("sentence", 1.5, 2.0)]
    cues = group_words_into_cues(words, max_words=10, max_seconds=999)
    assert len(cues) == 2
    assert cues[0].text == "Hello, world."
    assert cues[1].text == "Next sentence"


def test_time_offset_shifts_onto_a_global_timeline():
    words = [_word("later", 0.0, 1.0)]
    cues = group_words_into_cues(words, time_offset=10.0)
    assert cues[0].start == 10.0
    assert cues[0].end == 11.0


def test_start_index_continues_numbering_across_scenes():
    words = [_word("word", 0.0, 1.0)]
    cues = group_words_into_cues(words, start_index=5)
    assert cues[0].index == 5


def test_empty_word_list_yields_no_cues():
    assert group_words_into_cues([]) == []


def test_write_srt_produces_valid_format(tmp_path):
    words = [_word("Hello", 0.0, 0.5), _word("world.", 0.5, 1.2)]
    cues = group_words_into_cues(words)
    path = tmp_path / "captions.srt"
    write_srt(cues, path)

    content = path.read_text(encoding="utf-8")
    assert "1\n" in content
    assert "-->" in content
    assert "Hello world." in content
    assert "00:00:00,000" in content
