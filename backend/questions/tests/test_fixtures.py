from django.test import TestCase

from questions.models import AnswerOption, Category, ChoiceQuestion, NumericQuestion


class QuestionBankFixtureTests(TestCase):
    fixtures = ['questions/question_bank.json']

    def test_fixture_loads_expected_counts(self):
        self.assertEqual(Category.objects.count(), 6)
        self.assertEqual(ChoiceQuestion.objects.count(), 12)
        self.assertEqual(AnswerOption.objects.count(), 48)
        self.assertEqual(NumericQuestion.objects.count(), 12)

    def test_every_choice_question_is_valid(self):
        for question in ChoiceQuestion.objects.prefetch_related('answer_options'):
            with self.subTest(question=question.pk):
                question.full_clean()
                options = list(question.answer_options.all())
                self.assertEqual(len(options), 4)
                self.assertEqual(sum(option.is_correct for option in options), 1)

    def test_every_numeric_question_has_integer_answer(self):
        for question in NumericQuestion.objects.all():
            with self.subTest(question=question.pk):
                question.full_clean()
                self.assertIsInstance(question.correct_answer, int)

    def test_every_category_has_questions(self):
        for category in Category.objects.all():
            with self.subTest(category=category.name):
                self.assertTrue(category.choicequestions.exists())
                self.assertTrue(category.numericquestions.exists())
