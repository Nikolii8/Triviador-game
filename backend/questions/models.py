from django.core.exceptions import ValidationError
from django.db import models

REQUIRED_OPTION_COUNT = 4


def validate_answer_options(options):
    """Check a set of answer options: exactly four, exactly one of them correct.

    `options` is any iterable of objects with an `is_correct` attribute, so the
    same rule serves saved AnswerOption rows and unsaved admin inline forms.
    """
    options = list(options)
    errors = []
    if len(options) != REQUIRED_OPTION_COUNT:
        errors.append(
            ValidationError(
                'A choice question must have exactly %(required)d answer options (got %(count)d).',
                code='option_count',
                params={'required': REQUIRED_OPTION_COUNT, 'count': len(options)},
            )
        )
    correct_count = sum(1 for option in options if option.is_correct)
    if correct_count != 1:
        errors.append(
            ValidationError(
                'A choice question must have exactly one correct answer (got %(count)d).',
                code='correct_count',
                params={'count': correct_count},
            )
        )
    if errors:
        raise ValidationError(errors)


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'categories'

    def __str__(self) -> str:
        return self.name


class BaseQuestion(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='%(class)ss',
    )
    text = models.TextField()

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.text


class ChoiceQuestion(BaseQuestion):
    def clean(self):
        super().clean()
        # Options are rows pointing at this question, so they can only be checked once it is saved.
        if self.pk:
            validate_answer_options(self.answer_options.all())


class NumericQuestion(BaseQuestion):
    correct_answer = models.IntegerField()


class AnswerOption(models.Model):
    question = models.ForeignKey(
        ChoiceQuestion,
        on_delete=models.CASCADE,
        related_name='answer_options',
    )
    text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.text
