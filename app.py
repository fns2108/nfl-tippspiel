import os
import json
import requests
from datetime import datetime, timezone
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'change_this_to_something_secret'  # Required for sessions

# ----------------------------------------------------------------------------
# DATA MANAGEMENT
# ----------------------------------------------------------------------------
PICKS_FILE = 'picks.json'
USERS_FILE = 'users.json'

def load_json(filename):
    if not os.path.exists(filename):
        return {}
    with open(filename, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

def get_espn_schedule(week, year=None):
    """Fetches schedule and results from ESPN API."""
    if year is None:
        year = datetime.now().year
        if datetime.now().month < 3:
            year -= 1
            
    url = "http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    params = {
        'week': week,
        'year': year,
        'seasontype': 2,
        'limit': 100
    }
    try:
        r = requests.get(url, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Error fetching ESPN data: {e}")
        return {'events': []}

def get_current_week():
    """Helper to find the current NFL week from ESPN."""
    try:
        url = "http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
        r = requests.get(url)
        data = r.json()
        return data.get('week', {}).get('number', 1)
    except:
        return 1

def determine_winners(games):
    """Parses ESPN data to find winners of completed games."""
    winners = {}  # game_id: team_id
    if not games or 'events' not in games: 
        return winners
        
    for event in games.get('events', []):
        competition = event['competitions'][0]
        if competition['status']['type']['completed']:
            for competitor in competition['competitors']:
                if competitor.get('winner', False):
                    winners[str(event['id'])] = competitor['team']['id']
    return winners

# ----------------------------------------------------------------------------
# ROUTES
# ----------------------------------------------------------------------------

@app.route('/')
def index():
    if 'user' not in session:
        return render_template('login.html')
    current_week = get_current_week()
    return redirect(url_for('week_view', week=current_week))

@app.route('/check-user', methods=['POST'])
def check_user():
    """AJAX endpoint to see if a username exists."""
    data = request.get_json()
    username = data.get('username', '').strip()
    if not username:
        return jsonify({'error': 'Username required'}), 400
    
    users = load_json(USERS_FILE)
    exists = username in users
    return jsonify({'exists': exists})

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username', '').strip()
    password = request.form.get('password')
    
    if not username or not password:
        flash("Username and password are required.")
        return redirect('/')

    users = load_json(USERS_FILE)
    
    if username not in users:
        # Create new user
        users[username] = generate_password_hash(password)
        save_json(USERS_FILE, users)
        session['user'] = username
        flash(f"Welcome to the league, {username}! Your account is created.")
    else:
        # Check existing user
        if check_password_hash(users[username], password):
            session['user'] = username
        else:
            flash("Incorrect password. Please try again.")
            return redirect('/')
            
    return redirect('/')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')

@app.route('/week/<int:week>', methods=['GET', 'POST'])
def week_view(week):
    if 'user' not in session:
        return redirect('/')
    
    user = session['user']
    all_data = load_json(PICKS_FILE)
    
    week_str = str(week)
    if week_str not in all_data:
        all_data[week_str] = {}
    if user not in all_data[week_str]:
        all_data[week_str][user] = {}

    schedule = get_espn_schedule(week)
    now = datetime.now(timezone.utc)

    if request.method == 'POST':
        game_start_times = {}
        for event in schedule.get('events', []):
            try:
                start_time = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                game_start_times[str(event['id'])] = start_time
            except ValueError:
                continue

        saved_count = 0
        blocked_count = 0
        for game_id, team_id in request.form.items():
            kickoff = game_start_times.get(game_id)
            if not kickoff or now < kickoff:
                all_data[week_str][user][game_id] = team_id
                saved_count += 1
            else:
                blocked_count += 1
        
        save_json(PICKS_FILE, all_data)
        
        if blocked_count > 0:
            flash(f"Saved {saved_count} picks. {blocked_count} games were already locked.")
        else:
            flash(f"Picks successfully saved for Week {week}!")
            
        return redirect(url_for('week_view', week=week))

    user_picks = all_data[week_str].get(user, {})
    return render_template('week.html', 
                           week=week, 
                           schedule=schedule, 
                           user_picks=user_picks,
                           current_time=now)

@app.route('/scoreboard')
def scoreboard():
    if 'user' not in session:
        return redirect('/')
    
    all_data = load_json(PICKS_FILE)
    current_week = get_current_week()
    last_week = current_week - 1 if current_week > 1 else 1
    
    all_players = set()
    for week_data in all_data.values():
        all_players.update(week_data.keys())
    all_players = sorted(list(all_players))
    selected_player = request.args.get('player', session['user'])

    season_totals = {}
    weekly_results = {str(current_week): {}, str(last_week): {}}
    
    curr_winners = determine_winners(get_espn_schedule(current_week))
    last_winners = determine_winners(get_espn_schedule(last_week))
    
    team_stats = {}
    total_picks = 0
    total_wins = 0

    for week_str, week_picks in all_data.items():
        schedule = get_espn_schedule(int(week_str)) 
        winners = determine_winners(schedule)
        
        team_lookup = {}
        for event in schedule.get('events', []):
            for competitor in event['competitions'][0]['competitors']:
                tid = competitor['team']['id']
                team_lookup[tid] = {
                    'name': competitor['team']['displayName'],
                    'logo': competitor['team'].get('logo', 'https://a.espncdn.com/i/teamlogos/nfl/500/nfl.png')
                }

        for player, picks in week_picks.items():
            if player not in season_totals:
                season_totals[player] = {'correct': 0, 'weeks_played': 0}
            
            week_score = 0
            for game_id, picked_team_id in picks.items():
                is_win = game_id in winners and winners[game_id] == picked_team_id
                if is_win:
                    week_score += 1
                
                if player == selected_player:
                    total_picks += 1
                    if is_win: total_wins += 1
                    if picked_team_id not in team_stats:
                        info = team_lookup.get(picked_team_id, {'name': 'Unknown', 'logo': ''})
                        team_stats[picked_team_id] = {'name': info['name'], 'logo': info['logo'], 'times_picked': 0, 'wins': 0}
                    team_stats[picked_team_id]['times_picked'] += 1
                    if is_win: team_stats[picked_team_id]['wins'] += 1
            
            if week_str in [str(current_week), str(last_week)]:
                weekly_results[week_str][player] = week_score
            
            season_totals[player]['correct'] += week_score
            season_totals[player]['weeks_played'] += 1

    sorted_totals = dict(sorted(season_totals.items(), key=lambda item: item[1]['correct'], reverse=True))
    win_rate = round((total_wins / total_picks * 100), 1) if total_picks > 0 else 0
    fav_team = max(team_stats, key=lambda x: team_stats[x]['times_picked'], default=None)
    fav_team_name = team_stats[fav_team]['name'] if fav_team else "N/A"

    return render_template('scoreboard.html', 
                           season_totals=sorted_totals, 
                           weekly_results=weekly_results,
                           current_week=current_week,
                           last_week=last_week,
                           all_players=all_players,
                           selected_player=selected_player,
                           team_stats=team_stats,
                           total_picks=total_picks,
                           win_rate=win_rate,
                           fav_team=fav_team_name)

if __name__ == '__main__':
    if not os.path.exists('templates'):
        print("Warning: 'templates' folder not found.")
    app.run(debug=True, port=5000)