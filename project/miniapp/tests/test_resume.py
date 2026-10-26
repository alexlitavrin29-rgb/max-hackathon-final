"""No-network PDF output and bounded structured resume validation."""
from io import BytesIO

import pytest
from pydantic import ValidationError

from project.miniapp.resume_api import PutResume, ResumeState, resume_pdf


def test_resume_fields_are_bounded_strict_and_not_arbitrary_profile_data():
    for state in ({'passport': 'not accepted'}, {'name': 'x' * 241},
                  {'experience': 'x' * 4001}, {'name': '\0'}, {'name': '\ud800'},
                  {'no_experience': 1}):
        with pytest.raises(ValidationError):
            ResumeState.model_validate(state)
    for version in (True, -1, '0', 2**63):
        with pytest.raises(ValidationError):
            PutResume.model_validate({'expected_version': version, 'state': {}})
    assert ResumeState(name=' Анна ').name == 'Анна'


def test_real_pdf_embeds_cyrillic_escapes_markup_and_wraps_long_pages():
    pypdf = pytest.importorskip('pypdf')
    state = ResumeState(name='Анна Иванова <Иванова>', city='Казань', desired_role='Повар',
                        contacts='anna@example.test', education='Колледж\nПоварское дело',
                        no_experience=True, experience='Не должно экспортироваться',
                        skills=('Готовлю супы <script>alert(1)</script> & салаты.\n' * 70)).model_dump()
    output = resume_pdf(state)
    assert output.startswith(b'%PDF-')
    pdf = pypdf.PdfReader(BytesIO(output))
    assert len(pdf.pages) >= 2
    text = '\n'.join(page.extract_text() for page in pdf.pages)
    for expected in ('Анна Иванова <Иванова>', 'Казань', 'Пока нет опыта работы.',
                     'Готовлю супы <script>alert(1)</script> & салаты.', 'anna@example.test'):
        assert expected in text
    assert 'Не должно экспортироваться' not in text
    embedded = [font.get_object() for page in pdf.pages for font in page['/Resources']['/Font'].get_object().values()]
    assert any('/ToUnicode' in font and '/FontFile2' in font['/FontDescriptor'] for font in embedded if '/FontDescriptor' in font)


def test_pdf_handles_single_long_unbroken_text():
    output = resume_pdf(ResumeState(name='А' * 240, about='д' * 4000).model_dump())
    assert output.startswith(b'%PDF-') and output.rstrip().endswith(b'%%EOF')
