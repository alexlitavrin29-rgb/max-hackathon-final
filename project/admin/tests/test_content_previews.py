"""Reader-facing boundaries: legal abbreviations, URLs, lists and long sentences."""
from project.llm.services.content_text import sentence_preview


def test_long_first_sentence_stays_complete_without_ellipsis():
    first = 'Подготовь ' + 'документы и подтверждения ' * 20 + 'перед обращением.'
    assert sentence_preview(first + ' Потом подай заявление.', 80) == first


def test_legal_abbreviations_initials_decimal_and_labeled_links_are_not_boundaries():
    first = ('По ст. 8 закона № 159-ФЗ и п. 2 заявления уточни сумму 12.5 руб. '
             'у А. В. Иванова и открой [памятку](https://example.org/path_(law)?v=1.2).')
    assert sentence_preview(first + ' Следующий шаг.', 20) == first


def test_preview_keeps_heading_with_sentence_and_whole_list():
    assert sentence_preview('Как начать?\n\nПодготовь документы. Потом обратись в МВД.', 25) == 'Как начать?\nПодготовь документы.'
    # A heading without punctuation belongs to its explanatory sentence.
    assert sentence_preview('Подготовка\n\nСобери документы и фотографии. Подай заявление.', 10) == 'Подготовка\nСобери документы и фотографии.'
    checklist = 'Проверь:\n• имя и адрес;\n• срок и сумму;\n• подписи сторон.'
    assert sentence_preview(checklist + '\n\nСохрани копию.', 12) == checklist


def test_no_incomplete_trailing_fragment_is_added_to_preview():
    assert sentence_preview('Подай заявление. Незавершённый фрагмент', 240) == 'Подай заявление.'
    assert sentence_preview('Уточни документы! Сохрани ответ? Дождись решения.', 40) == 'Уточни документы! Сохрани ответ?'
