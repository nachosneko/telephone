import discord
from discord.ext import commands, tasks
from discord import app_commands
import random

from datetime import datetime, timedelta

intents = discord.Intents.all()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="?", intents=intents)

participants = []
taken_turns = set()
current_turn = None
clip_deadline = None
chain_log = []
results_channel_id = None  # Stores the channel ID for result announcements

def reset_game():
    global participants, taken_turns, current_turn, clip_deadline, chain_log
    participants = []
    taken_turns = set()
    current_turn = None
    clip_deadline = None
    chain_log = []

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
        clip_deadline = datetime.utcnow() + timedelta(seconds=15)
        chain_log.append((self.clip_view.author, receiver, self.clip_url))

        try:
            await receiver.send(
                f"\U0001F3B5 Hello! You're next in line for the telephone!\n"
                f"Your clip: {self.clip_url}\n"
                f"You got 12 hours to make a clip. Use `/send` to pass it on."
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
            print("❗ Tried to edit a message that no longer exists (likely deleted or ephemeral).")


@tasks.loop(seconds=5)
async def check_deadlines():
    global current_turn, clip_deadline
    if current_turn and clip_deadline and datetime.utcnow() > clip_deadline:
        current_user = discord.utils.get(bot.get_all_members(), id=current_turn)
        if current_user:
            available = [p for p in participants if p.id not in taken_turns]
            if available:
                next_user = random.choice(available)
                taken_turns.add(next_user.id)
                current_turn = next_user.id
                clip_deadline = datetime.utcnow() + timedelta(seconds=15)
                try:
                    await next_user.send(
                        "\U0001F3B5 You were automatically chosen to continue the telephone game. Respond using `/send` within 12 hours."
                    )
                except:
                    pass
            else:
                await send_results(bot.guilds)
                reset_game()

@bot.event
async def on_ready():
    print(f'\u2705 Logged in as {bot.user}')
    check_deadlines.start()

@bot.tree.command(name="register", description="Register yourself to play the telephone game.")
async def register(interaction: discord.Interaction):
    if interaction.user not in participants:
        participants.append(interaction.user)
        await interaction.response.send_message("\u2705 You're now registered!", ephemeral=True)
    else:
        await interaction.response.send_message("\u2757 You're already registered.")

@bot.tree.command(name="send", description="Send your clip to the next person.")
@app_commands.describe(clip="link", artist="artist name")
async def send(interaction: discord.Interaction, clip: str, artist: str):
    global current_turn, clip_deadline
    if interaction.user.id != current_turn:
        await interaction.response.send_message("\u2757 It's not your turn yet.", ephemeral=True)
        return

    clip_with_artist = f"{clip} (Artist: {artist})"
    view = ClipView(interaction.user, clip_with_artist)

    if view.no_choices:
        chain_log.append((interaction.user, interaction.user, clip_with_artist))
        await send_results(interaction.client.guilds)
        reset_game()
    else:
        await interaction.response.send_message("Choose who to send the clip to:", view=view, ephemeral=True)

@bot.tree.command(name="start", description="Admin only: start the game with a specific player.")
@app_commands.describe(player="The player to start the game with.")
@app_commands.checks.has_permissions(administrator=True)
async def start(interaction: discord.Interaction, player: discord.User):
    global current_turn, clip_deadline

    if player not in participants:
        await interaction.response.send_message(f"\u2757 {player.display_name} is not registered for the game.", ephemeral=True)
        return

    if player.id in taken_turns:
        await interaction.response.send_message(f"\u2757 {player.display_name} has already participated.", ephemeral=True)
        return

    taken_turns.add(player.id)
    current_turn = player.id
    clip_deadline = datetime.utcnow() + timedelta(seconds=15)

    try:
        await player.send(
            f"\U0001F3B5 You're first in the chain for the telephone! You've got within 12 hours to send it forward.\n"
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
    await ctx.send(f"\U0001F4E2")

@bot.command(name="registered", aliases=["regs"])
async def registered(ctx):
    await ctx.send(f"\U0001F465 {len(participants)} players are registered for the telephone game.")

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

async def send_results(guilds):
    if not chain_log:
        return

    embed = discord.Embed(title="\U0001F4DC Game Results", color=discord.Color.blue())
    max_entries_per_page = 10
    pages = [embed.copy() for _ in range((len(chain_log) + max_entries_per_page - 1) // max_entries_per_page)]

    for i, (sender, receiver, clip_url) in enumerate(chain_log, 1):
        page_index = (i - 1) // max_entries_per_page
        pages[page_index].add_field(
            name=f"#{i}: {sender.display_name} \u2794 {receiver.display_name}",
            value=clip_url, inline=False
        )

    with open("telephone_results.txt", "w", encoding="utf-8") as f:
        for i, (sender, receiver, clip_url) in enumerate(chain_log, 1):
            f.write(f"#{i}: {sender.display_name} \u2794 {receiver.display_name}\n{clip_url}\n\n")

    for guild in guilds:
        channel = guild.get_channel(results_channel_id)
        if channel:
            await channel.send("@everyone \U0001F4E3 The telephone game has ended! Here are the results:")
            for page in pages:
                await channel.send(embed=page)
            await channel.send(file=discord.File("telephone_results.txt"))

bot.run('BOT_SECRET_TOKEN')