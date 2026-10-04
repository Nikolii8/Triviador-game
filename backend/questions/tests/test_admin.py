from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from questions.models import Category, ChoiceQuestion

User = get_user_model()


class ChoiceQuestionAdminTests(TestCase):
    def setUp(self):
        admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='example-password')
        self.client.force_login(admin)
        self.category = Category.objects.create(name='Наука')
        self.url = reverse('admin:questions_choicequestion_add')

    def post_question(self, options):
        data = {
            'category': self.category.pk,
            'text': 'Кой е химичният символ на златото?',
            'answer_options-TOTAL_FORMS': len(options),
            'answer_options-INITIAL_FORMS': 0,
            'answer_options-MIN_NUM_FORMS': 4,
            'answer_options-MAX_NUM_FORMS': 4,
        }
        for index, (text, is_correct) in enumerate(options):
            data[f'answer_options-{index}-text'] = text
            if is_correct:
                data[f'answer_options-{index}-is_correct'] = 'on'
        return self.client.post(self.url, data)

    def test_admin_saves_valid_question_with_inline_options(self):
        response = self.post_question([('Au', True), ('Ag', False), ('Fe', False), ('Zn', False)])

        self.assertEqual(response.status_code, 302)
        question = ChoiceQuestion.objects.get()
        self.assertEqual(question.answer_options.count(), 4)

    def test_admin_rejects_invalid_options(self):
        cases = {
            'two correct': [('Au', True), ('Ag', True), ('Fe', False), ('Zn', False)],
            'no correct': [('Au', False), ('Ag', False), ('Fe', False), ('Zn', False)],
            'three options': [('Au', True), ('Ag', False), ('Fe', False)],
        }
        for label, options in cases.items():
            with self.subTest(label):
                response = self.post_question(options)

                self.assertEqual(response.status_code, 200)
                self.assertFalse(ChoiceQuestion.objects.exists())

    def test_admin_changelists_load(self):
        for model in ('category', 'choicequestion', 'numericquestion'):
            with self.subTest(model=model):
                response = self.client.get(reverse(f'admin:questions_{model}_changelist'), {'q': 'злато'})
                self.assertEqual(response.status_code, 200)
