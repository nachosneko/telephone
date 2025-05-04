import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

from db import init_db, save_clip, load_chain_log, archive_database  

load_dotenv(".env.secret")
init_db()

intents = discord.Intents.all()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="?", intents=intents)

participants = []
taken_turns = set()
current_turn = None
clip_deadline = None
chain_log = []
results_channel_id = None
current_clip_url = None
clip_deadline_hours = 6

def format_deadline(hours: float):
    if hours >= 1:
        return f"{hours:.0f} hour(s)"
    elif hours >= 1/60:
        return f"{hours * 60:.0f} minute(s)"
    else:
        return f"{hours * 3600:.0f} second(s)"

def reset_game():
    global participants, taken_turns, current_turn, clip_deadline, chain_log, current_clip_url, results_channel_id
    archive_database()
    participants = []
    taken_turns = set()
    current_turn = None
    clip_deadline = None
    chain_log = []
    current_clip_url = None
    results_channel_id = None

class ClipView(discord.ui.View):
    def __init__(self, author, clip_url):
        super().__init__(timeout=None)
        self.author = author
        self.clip_url = clip_url
        self.choices = self.generate_choices()

        if not self.choices:
            self.no_choices = True
        else:
            self.no_choices = False
            for user in self.choices:
                self.add_item(self.make_button(user))

    def generate_choices(self):
        available = [p for p in participants if p.id not in taken_turns and p.id != self.author.id]
        return random.sample(available, min(4, len(available)))

    def make_button(self, user):
        return ClipButton(label=user.display_name, user=user, clip_view=self, clip_url=self.clip_url)

class ClipButton(discord.ui.Button):
    def __init__(self, label, user, clip_view, clip_url):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.user = user
        self.clip_view = clip_view
        self.clip_url = clip_url

    async def callback(self, interaction: discord.Interaction):
        global current_turn, clip_deadline

        if interaction.user.id != self.clip_view.author.id:
            await interaction.response.send_message("You can't choose the next person.", ephemeral=True)
            return

        receiver = self.user
        taken_turns.add(receiver.id)
        current_turn = receiver.id
        clip_deadline = datetime.utcnow() + timedelta(hours=clip_deadline_hours)

        if chain_log and chain_log[-1][1] is None:
            last_entry = chain_log.pop()
            chain_log.append((last_entry[0], receiver, last_entry[2], last_entry[3], last_entry[4]))
            save_clip(last_entry[0].id, receiver.id, last_entry[2], last_entry[3], last_entry[4])
            print(f"🔗 Updated chain: {last_entry[0].display_name} -> {receiver.display_name}")

        try:
            await receiver.send(
                f"\U0001F3B5 Hello! You're next in line for the telephone.\n"
                f"Your clip: {self.clip_view.clip_url}\n"
                f"You got {format_deadline(clip_deadline_hours)} to make a clip. Use `/send` to pass it on."
            )
            await interaction.response.send_message(
                f"Clip sent to {receiver.display_name}.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"Couldn't DM {receiver.display_name}. Maybe DMs are closed?", ephemeral=True
            )

        for child in self.clip_view.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self.clip_view)
        except discord.NotFound:
            print("❗ Tried to edit a message that no longer exists.")

@tasks.loop(seconds=10)
async def check_deadlines():
    global current_turn, clip_deadline

    if current_turn and clip_deadline and datetime.utcnow() > clip_deadline:
        current_user = discord.utils.get(bot.get_all_members(), id=current_turn)
        if current_user:
            available = [p for p in participants if p.id not in taken_turns]
            if available:
                next_user = random.choice(available)
                taken_turns.add(next_user.id)

                if chain_log and chain_log[-1][1] is None:
                    last_entry = chain_log.pop()
                    chain_log.append((last_entry[0], next_user, last_entry[2], last_entry[3], last_entry[4]))
                    save_clip(last_entry[0].id, next_user.id, last_entry[2], last_entry[3], last_entry[4])
                    print(f"⚠️ {current_user.display_name} missed the deadline. Passed to {next_user.display_name}")

                current_turn = next_user.id
                clip_deadline = datetime.utcnow() + timedelta(hours=clip_deadline_hours)

                try:
                    await next_user.send(
                        f"\U0001F3B5 Previous person missed the deadline. Hence you were chosen to continue the chain. "
                        f"Respond with `/send` within {format_deadline(clip_deadline_hours)}.\n"
                        f"Clip: {current_clip_url}"
                    )

                except:
                    pass
            else:
                await send_results(bot.guilds)
                reset_game()

@bot.event
async def on_ready():
    global chain_log
    print(f'✅ Logged in as {bot.user}')

    chain_log = load_chain_log(bot)
    print(f"📜 Loaded {len(chain_log)} entries from previous game.")

    check_deadlines.start()

@bot.tree.command(name="register", description="Register yourself to play the telephone game.")
async def register(interaction: discord.Interaction):
    if interaction.user not in participants:
        participants.append(interaction.user)
        await interaction.response.send_message("✅ You're now registered!", ephemeral=True)
    else:
        if interaction.user.id in taken_turns:
            taken_turns.discard(interaction.user.id)
            await interaction.response.send_message("🔁 You're back in the queue for another turn!", ephemeral=True)
        else:
            await interaction.response.send_message("❗ You're already registered and waiting!", ephemeral=True)

@bot.tree.command(name="leave", description="Leave the game.")
async def leave(interaction: discord.Interaction):
    global participants, taken_turns
    if interaction.user in participants:
        participants.remove(interaction.user)
        taken_turns.discard(interaction.user.id)
        await interaction.response.send_message("👋 You've been removed from the game.", ephemeral=True)
    else:
        await interaction.response.send_message("❗ You weren't registered in the game.", ephemeral=True)

@bot.tree.command(name="send", description="Send your clip to the next person.")
@app_commands.describe(clip="link", artist="artist name", song="song name")
async def send(interaction: discord.Interaction, clip: str, artist: str, song: str):
    global current_turn, clip_deadline, current_clip_url

    if interaction.user.id != current_turn:
        await interaction.response.send_message("\u2757 It's not your turn yet.", ephemeral=True)
        return

    current_clip_url = clip
    view = ClipView(interaction.user, clip)

    if view.no_choices:
        chain_log.append((interaction.user, interaction.user, clip, artist, song))
        save_clip(interaction.user.id, interaction.user.id, clip, artist, song)
        
        await interaction.response.send_message("✅ Clip submitted. Since you're last, the game will now end shortly. Wait for the host to post the results~", ephemeral=True)
        
        await send_results(interaction.client.guilds)
        reset_game()
    else:
        chain_log.append((interaction.user, None, clip, artist, song))  
        await interaction.user.send("Choose who to send the clip to:", view=view)
        await interaction.response.send_message("✅ Clip submitted. Check your DMs to pick the next person!", ephemeral=True)
        print(f"🎬 Clip sent by {interaction.user.display_name}. Awaiting next recipient...")


@bot.tree.command(name="start", description="Admin only: start the game with a specific player.")
@app_commands.describe(player="The player to start the game with.")
@app_commands.checks.has_permissions(administrator=True)
async def start(interaction: discord.Interaction, player: discord.User):
    global current_turn, clip_deadline

    if player not in participants:
        await interaction.response.send_message(f"\u2757 {player.display_name} did not opt in for the game.", ephemeral=True)
        return

    if player.id in taken_turns:
        await interaction.response.send_message(f"\u2757 {player.display_name} has already taken their turn.", ephemeral=True)
        return

    taken_turns.add(player.id)
    current_turn = player.id
    clip_deadline = datetime.utcnow() + timedelta(hours=clip_deadline_hours)

    try:
        await player.send(
            f"\U0001F3B5 You're first in the chain for the telephone! You've got {format_deadline(clip_deadline_hours)} to send it forward.\n"
            f"Use `/send` to pass your clip when you're ready."
        )
        await interaction.response.send_message(f"{player.display_name} was chosen to start the game!", ephemeral=True)
    except:
        await interaction.response.send_message(f"Couldn't DM {player.display_name}.", ephemeral=True)

@bot.command(name="setchannel")
@commands.has_permissions(administrator=True)
async def setchannel(ctx):
    global results_channel_id
    results_channel_id = ctx.channel.id
    await ctx.send(f"\U0001F4E2 Results channel set!")

@bot.command(name="registered", aliases=["p"])
async def registered(ctx):
    await ctx.send(f"\U0001F465 {len(participants)} players are available for the telephone game.")

@bot.command(name="remaining", aliases=["rem","left"])
async def remaining(ctx):
    remaining_count = len([p for p in participants if p.id not in taken_turns])
    await ctx.send(f"\u23F3 {remaining_count} players are still waiting for their turn.")

@bot.command(name="sync")
@commands.is_owner()
async def sync_commands(ctx, scope: str = "global"):
    if scope == "global":
        synced = await bot.tree.sync()
        await ctx.send(f"\U0001F310 Synced {len(synced)} commands globally.")
    elif scope == "guild":
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"\U0001F3E0 Synced {len(synced)} commands to this guild ({ctx.guild.name}).")
    elif scope == "custom":
        guild_ids = [366643056138518529, 920092394945384508]
        total = 0
        for gid in guild_ids:
            guild = discord.Object(id=gid)
            synced = await bot.tree.sync(guild=guild)
            total += len(synced)
        await ctx.send(f"\U0001F527 Synced commands to {len(guild_ids)} custom guilds ({total} total commands).")
    else:
        await ctx.send("\u2753 Unknown sync scope. Use `global`, `guild`, or `custom`.")

@bot.command(name="deadline")
@commands.has_permissions(administrator=True)
async def deadline(ctx, time: str):
    """
    Set the deadline duration.
    Usage:
    ?deadline 6h  → 6 hours
    ?deadline 30m → 30 minutes
    ?deadline 45s → 45 seconds
    """

    global clip_deadline_hours

    time = time.lower().strip()

    try:
        if time.endswith("h"):
            clip_deadline_hours = float(time[:-1])
        elif time.endswith("m"):
            clip_deadline_hours = float(time[:-1]) / 60
        elif time.endswith("s"):
            clip_deadline_hours = float(time[:-1]) / 3600
        else:
            await ctx.send("❗ Invalid time format. Use `h`, `m`, or `s` (e.g., `6h`, `30m`, `45s`).")
            return

        readable = timedelta(hours=clip_deadline_hours)
        await ctx.send(f"⏳ Deadline updated! New deadline: {readable}.")
    except ValueError:
        await ctx.send("❗ Couldn't parse time. Use formats like `6h`, `30m`, or `45s`.")


@bot.command(name="current")
@commands.has_permissions(administrator=True)
async def current(ctx):
    if current_turn:
        current_user = discord.utils.get(bot.get_all_members(), id=current_turn)
        if current_user:
            await ctx.send(f"🎯 Current turn holder: {current_user.display_name}")
        else:
            await ctx.send("❗ Couldn't find who currently has the clip.")
    else:
        await ctx.send("❗ No one currently holds the clip.")

async def send_results(guilds):
    if not chain_log:
        return

    embed = discord.Embed(title="\U0001F4DC Game Results", color=discord.Color.blue())
    max_entries_per_page = 10
    pages = [embed.copy() for _ in range((len(chain_log) + max_entries_per_page - 1) // max_entries_per_page)]

    for i, (sender, receiver, clip_url, artist, song) in enumerate(chain_log, 1):
        sender_name = sender.display_name if sender else "Unknown"
        receiver_name = receiver.display_name if receiver else "—"
        page_index = (i - 1) // max_entries_per_page
        pages[page_index].add_field(
            name=f"#{i}: {sender_name} ➔ {receiver_name}",
            value=f"Clip: {clip_url}\nArtist: {artist}\nSong: {song}",
            inline=False
        )

    with open("telephone_results.txt", "w", encoding="utf-8") as f:
        for i, (sender, receiver, clip_url, artist, song) in enumerate(chain_log, 1):
            sender_name = sender.display_name if sender else "Unknown"
            receiver_name = receiver.display_name if receiver else "—"
            f.write(f"#{i}: {sender_name} ➔ {receiver_name}\nClip: {clip_url}\nArtist: {artist}\nSong: {song}\n\n")

    for guild in guilds:
        if results_channel_id is None:
            print(f"❗ No results channel set for guild: {guild.name}")
            continue
        channel = guild.get_channel(results_channel_id)
        if channel:
            await channel.send("@everyone \U0001F4E3 The telephone game has ended! Here are the results:")
            for page in pages:
                await channel.send(embed=page)
            await channel.send(file=discord.File("telephone_results.txt"))
        else:
            print(f"❗ Could not find results channel with ID {results_channel_id} in guild: {guild.name}")

bot.run(os.getenv("BOT_TOKEN"))
