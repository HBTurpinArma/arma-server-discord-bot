from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import re

class MissionSubmissionModal(discord.ui.Modal, title="Mission Submission Form"):
    def __init__(self, bot, mission_pbo: discord.Attachment) -> None:
        super().__init__(title=f"Mission Submission Form")
        self.bot = bot
        self.mission_pbo = mission_pbo

    mission_title = discord.ui.TextInput(style=discord.TextStyle.short, label="Mission Title", required=True, placeholder="Operation Stinky")
    mission_description = discord.ui.TextInput(style=discord.TextStyle.long, label="Description", required=False, placeholder="A short description of the mission and objectives.")
    # mission_map = discord.ui.TextInput(style=discord.TextStyle.short, label="Map", required=True, placeholder="Chernarus/Altis/Stratis")
    mission_briefing = discord.ui.TextInput(style=discord.TextStyle.short, label="Briefing", required=False, placeholder="https://docs.google.com/document/d/...")
    mission_conceptboard = discord.ui.TextInput(style=discord.TextStyle.short, label="Conceptboard", required=False, placeholder="https://app.conceptboard.com/board/...")
    mission_notes = discord.ui.TextInput(style=discord.TextStyle.long, label="Notes", required=False, placeholder="Size? Faction? Uniform? Enemy? Anything else?")


    async def on_submit(self, interaction: discord.Interaction):
        answer_title = str(self.mission_title) or ""
        answer_description = str(self.mission_description) or ""
        answer_briefing = str(self.mission_briefing) or ""
        answer_conceptboard = str(self.mission_conceptboard) or ""
        answer_notes = str(self.mission_notes) or ""

        #Obtain the map name automatically from the mission file suffix.
        answer_map = re.search(r"\.(\w+)(?:\.pbo)?$", self.mission_pbo.filename).group(1)

        #Get users to mention, should be the user who submitted the mission and the forum moderators.
        mentions = f"<@{interaction.user.id}>"
        for moderators in self.bot.config['discord']['mission_development']['forum_moderators']:
            mentions += f" <@&{moderators}>"
        answer_post = f"||{mentions}||\n# {answer_title}\n{answer_description}\n\n**Map:** {answer_map}\n**Briefing:** {answer_briefing}\n**Conceptboard:** {answer_conceptboard}\n\n**Additional Notes:**{answer_notes}"

        forum_channel = self.bot.get_channel(int(self.bot.config['discord']['mission_development']['forum_channel_id']))
        tags = [forum_channel.get_tag(int(self.bot.config['discord']['mission_development']['forum_tags']['pending_review']))]

        # Prepare pbo file for upload
        pbo_file = await self.mission_pbo.to_file(use_cached=False, spoiler=False)

        # Create thread
        self.post = await forum_channel.create_thread(name=f"Mission: {answer_title}", applied_tags=tags, content=answer_post, file=pbo_file)

        # Lets ping the mission author and add a quick note to ensure all information has been provided.
        await self.post[0].send(f"<@{interaction.user.id}>, thanks for your submission, can you ensure all the below questions have been answered in the above thread, if not add extra information below.\n- Whats is the size of the mission, is it fit for squad, platoon or battalion?\n- What faction are we playing as? What uniform should be used?\n- What assets are available? Are there any vehicles, air support?\n- What faction are we playing against? What's their name in Zeus/Editor?\n- What is the enemy strength? How many vehicles, infantry, air?\n- Has the concept board been updated with the latest information?\n- Is there a conceptboard with visuals for HVTs/objectives?\n\nAdditionally if you have any questions or need to upload new versions, do just add to this thread directly.")

        # Lets ping the head of misdev to ensure they are aware of the submission and can review it.
        mentions = ""
        for moderators in self.bot.config['discord']['mission_development']['forum_moderators']:
            mentions += f" <@&{moderators}>"

        await self.post[0].send(f"""{mentions} will look to review this mission submission as soon as possible, please be patient.""")

        # Respond to the interaction
        await interaction.response.send_message(embed=discord.Embed(description=f"Thanks for your mission submission, misdev will review it and schedule it to be played. You can see the mission submission thread here: <#{self.post[1].id}>", color=0xBEBEFE, ), ephemeral=True)

        self.stop()

class MisdevOffice(commands.Cog, name="misdev"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="mission_submission", description="Submit a mission to misdev who will review and schedule it to be played.", )
    @app_commands.describe(mission_pbo="The .pbo file for mission you are submitting.")
    async def mission_submission(self, interaction: discord.Interaction, mission_pbo: discord.Attachment) -> None:
        # Return not configured error embed to interaction if executed in the wrong guild.
        if interaction.guild.id != self.bot.config['discord']['guild_id']:
            await interaction.response.send_message(embed=await not_configured_embed(self), ephemeral=True)
            return

        if not mission_pbo.filename.endswith(".pbo"):
            await interaction.response.send_message(embed=discord.Embed(description=f"Please upload a valid `.pbo` file.", color=0xFF2B2B), ephemeral=True)
            return

        # Send the modal the user and wait for a response.
        mission_submission_modal = MissionSubmissionModal(self.bot, mission_pbo)
        await interaction.response.send_modal(mission_submission_modal)
        # await mission_submission_modal.wait()
        # interaction = mission_submission_modal.interaction

    @commands.command(name="misdev_info", description="Post information embed for mission submission / help.")
    @commands.is_owner()
    async def sao_info(self, context: Context) -> None:
        embed = discord.Embed(title="Mission Forums",
                              description="If you are looking to submit a new mission use `/mission_submission` and provide your mission .pbo and all the relevant information via the discord form.\n\nIf you have an idea for a mission and want to discuss it, create a new thread with the idea tag in the mission forum.\n\nIf you are wanting to learn how to make missions, it might be worth checking on the guides in the forum or reaching out to misdev directly.",
                              color=0xBEBEFE)
        await context.send(embed=embed)

async def not_configured_embed(self):
    return discord.Embed(title=f"Command Unavailable", description=f"This command hasn't been configured for this server yet, please try again later.", color=0xFF2B2B, )


async def setup(bot) -> None:
    await bot.add_cog(MisdevOffice(bot))
