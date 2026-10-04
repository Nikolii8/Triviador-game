from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from questions.models import AnswerOption, Category, ChoiceQuestion, NumericQuestion


def make_choice_question(category, options=(('Канбера', True), ('Сидни', False), ('Мелбърн', False), ('Пърт', False))):
    question = ChoiceQuestion.objects.create(category=category, text='Коя е столицата на Австралия?')
    for text, is_correct in options:
        AnswerOption.objects.create(question=question, text=text, is_correct=is_correct)
    return question


class CategoryTests(TestCase):
    def test_create_category(self):
        category = Category.objects.create(name='География')

        category.full_clean()
        self.assertEqual(str(category), 'География')

    def test_category_name_must_be_unique(self):
        Category.objects.create(name='География')

        with self.assertRaises(ValidationError) as ctx:
            Category(name='География').full_clean()
        self.assertIn('name', ctx.exception.message_dict)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(name='География')

    def test_category_with_questions_cannot_be_deleted(self):
        cases = [
            ('choice', lambda category: make_choice_question(category)),
            ('numeric', lambda category: NumericQuestion.objects.create(category=category, text='Колко?', correct_answer=5)),
        ]
        for label, create_question in cases:
            with self.subTest(question_type=label):
                category = Category.objects.create(name=f'Категория {label}')
                create_question(category)

                with self.assertRaises(ProtectedError):
                    category.delete()
                self.assertTrue(Category.objects.filter(pk=category.pk).exists())


class ChoiceQuestionTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='География')

    def test_create_valid_choice_question(self):
        question = make_choice_question(self.category)

        question.full_clean()
        self.assertEqual(question.category, self.category)
        self.assertIn(question, self.category.choicequestions.all())
        self.assertEqual(question.answer_options.count(), 4)
        self.assertEqual(question.answer_options.get(is_correct=True).text, 'Канбера')

    def test_invalid_choice_questions(self):
        cases = {
            'three options': [('A', True), ('B', False), ('C', False)],
            'five options': [('A', True), ('B', False), ('C', False), ('D', False), ('E', False)],
            'no correct option': [('A', False), ('B', False), ('C', False), ('D', False)],
            'two correct options': [('A', True), ('B', True), ('C', False), ('D', False)],
        }
        for label, options in cases.items():
            with self.subTest(label):
                question = make_choice_question(self.category, options)

                with self.assertRaises(ValidationError):
                    question.full_clean()

    def test_answer_option_defaults_to_incorrect(self):
        question = ChoiceQuestion.objects.create(category=self.category, text='Въпрос')

        option = AnswerOption.objects.create(question=question, text='Отговор')

        self.assertFalse(option.is_correct)

    def test_deleting_question_deletes_its_answer_options(self):
        question = make_choice_question(self.category)
        other = make_choice_question(self.category)

        question.delete()

        self.assertFalse(AnswerOption.objects.filter(question_id=question.pk).exists())
        self.assertEqual(AnswerOption.objects.filter(question=other).count(), 4)


class NumericQuestionTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='История')

    def test_create_numeric_question(self):
        question = NumericQuestion.objects.create(
            category=self.category,
            text='През коя година пада Берлинската стена?',
            correct_answer=1989,
        )

        question.full_clean()
        self.assertEqual(question.correct_answer, 1989)
        self.assertIn(question, self.category.numericquestions.all())

    def test_correct_answer_is_required_and_must_be_integer(self):
        for value in (None, 'много'):
            with self.subTest(correct_answer=value):
                question = NumericQuestion(category=self.category, text='Колко?', correct_answer=value)

                with self.assertRaises(ValidationError) as ctx:
                    question.full_clean()
                self.assertIn('correct_answer', ctx.exception.message_dict)
