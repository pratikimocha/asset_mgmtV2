"""Public home page — no login required."""
from flask import Blueprint, render_template, session

bp = Blueprint('home', __name__)


@bp.route('/')
def index():
    user = session.get('user')
    return render_template('home.html', user=user)
