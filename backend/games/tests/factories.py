from django.contrib.auth import get_user_model

from games.models import Game, GamePlayer, Round
from questions.models import AnswerOption, Category, ChoiceQuestion, NumericQuestion

User = get_user_model()


def make_user(username):
    # No password: games tests never log in, and skipping hashing keeps them fast.
    return User.objects.create_user(username=username, email=f'{username}@example.com', password=None)


def make_game(creator=None, **fields):
    return Game.objects.create(created_by=creator or make_user('creator'), **fields)


def add_player(game, username, order):
    return GamePlayer.objects.create(game=game, user=make_user(username), player_order=order)


def make_choice_question(text='Коя е столицата на Австралия?'):
    category, _ = Category.objects.get_or_create(name='География')
    question = ChoiceQuestion.objects.create(category=category, text=text)
    for option_text, is_correct in (('Канбера', True), ('Сидни', False), ('Мелбърн', False), ('Пърт', False)):
        AnswerOption.objects.create(question=question, text=option_text, is_correct=is_correct)
    return question


def make_numeric_question(text='През коя година пада Берлинската стена?', answer=1989):
    category, _ = Category.objects.get_or_create(name='История')
    return NumericQuestion.objects.create(category=category, text=text, correct_answer=answer)


def make_choice_round(game, number=1, question=None, **fields):
    return Round.objects.create(
        game=game,
        number=number,
        choice_question=question or make_choice_question(),
        **fields,
    )


def make_numeric_round(game, number=1, question=None, **fields):
    return Round.objects.create(
        game=game,
        number=number,
        numeric_question=question or make_numeric_question(),
        **fields,
    )
