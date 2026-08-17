from backend.app.models import Caption
from backend.app.preprocessing import build_turns, deduplicate_caption_updates, split_clauses, split_sentences


def test_caption_update_keeps_longer_version_but_not_distant_repetition() -> None:
    captions = [
        Caption("CAP-1", "Nam", 0, 2000, "Em sẽ gửi kế hoạch"),
        Caption("CAP-2", "Nam", 1500, 3000, "Em sẽ gửi kế hoạch vào thứ Sáu"),
        Caption("CAP-3", "Nam", 10000, 12000, "Em sẽ gửi kế hoạch vào thứ Sáu"),
    ]
    result = deduplicate_caption_updates(captions)
    assert len(result) == 2
    assert result[0].text_raw.endswith("thứ Sáu")
    assert result[0].source_caption_ids == ["CAP-1", "CAP-2"]


def test_turn_sentence_and_clause_boundaries() -> None:
    captions = [Caption("CAP-1", "Nam", 0, 1000, "Phương chuẩn bị kế hoạch, còn Linh kiểm tra dữ liệu."), Caption("CAP-2", "Nam", 1200, 2000, "Khoan, đổi thành thứ Sáu tuần sau.")]
    turns = build_turns(captions)
    sentences = split_sentences(turns)
    clauses = split_clauses(sentences)
    assert len(turns) == 1
    assert any("đổi thành" in sentence.text_normalized for sentence in sentences)
    assert any("Linh kiểm tra" in clause.text_raw for clause in clauses)
