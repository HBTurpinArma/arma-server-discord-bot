from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import re


class MissionSubmissionModal(discord.ui.Modal):
    def __init__(self, bot, mission_pbo: discord.Attachment) -> None:
        super().__init__(title=f"Mission Submission Form")
        self.bot = bot
        self.mission_pbo = mission_pbo

    mission_title = discord.ui.TextInput(style=discord.TextStyle.short, label="Mission Title", required=True, placeholder="Operation Stinky")
    # mission_map = discord.ui.TextInput(style=discord.TextStyle.short, label="Map", required=True, placeholder="Chernarus/Altis/Stratis")
    mission_briefing = discord.ui.TextInput(style=discord.TextStyle.short, label="Briefing", required=True, placeholder="https://docs.google.com/document/d/...")
    mission_conceptboard = discord.ui.TextInput(style=discord.TextStyle.short, label="Conceptboard", required=True, placeholder="https://app.conceptboard.com/board/...")
    mission_notes = discord.ui.TextInput(style=discord.TextStyle.long, label="Notes", required=False, placeholder="What uniform should be used? Any special instructions? Is it battalion or squad sized? Any additional mods required?")


    async def on_submit(self, interaction: discord.Interaction):
        answer_title = str(self.mission_title) or ""
        answer_map = ""
        answer_briefing = str(self.mission_briefing) or ""
        answer_conceptboard = str(self.mission_conceptboard) or ""
        answer_notes = str(self.mission_notes) or ""

        #Obtain the map name automatically from the mission file suffix.
        if answer_map == "":
            answer_map = re.search(r"_(.*?)\.pbo", answer_title).group(1)

        #Get users to mention, should be the user who submitted the mission and the forum moderators.
        mentions = f"<@{interaction.user.id}>"
        for moderators in self.bot.config['discord']['mission_development']['forum_moderators']:
            mentions += f" <@&{moderators}>"
        answer_post = f"||{mentions}||\n# {answer_title}\n**Map:** {answer_map}\n**Briefing:** {answer_briefing}\n**Conceptboard:** {answer_conceptboard}\n\n**Additional Notes:**{answer_notes}"

        forum_channel = self.bot.get_channel(int(self.bot.config['discord']['mission_development']['forum_channel_id']))
        tags = [forum_channel.get_tag(int(self.bot.config['discord']['mission_development']['forum_tags']['pending_review']))]

        # Prepare pbo file for upload
        pbo_file = await self.mission_pbo.to_file(filename=self.mission_pbo.filename, use_cached=False, spoiler=False)

        # Create thread
        post = await forum_channel.create_thread(name=f"Mission: {answer_title}", applied_tags=tags, content=answer_post, file=pbo_file)

        # Respond to the interaction
        await interaction.response.send_message(embed=discord.Embed(description=f"Thanks for your mission submission, misdev will review it and schedule it to be played. You can see the mission submission thread here: <#{post[1].id}>", color=0xBEBEFE, ), ephemeral=True)

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

        # Send the modal the user and wait for a response.
        mission_submission_modal = MissionSubmissionModal(self.bot, mission_pbo)
        await interaction.response.send_modal(mission_submission_modal)
        await mission_submission_modal.wait()
        interaction = mission_submission_modal.interaction


async def not_configured_embed(self):
    return discord.Embed(title=f"Command Unavailable", description=f"This command hasn't been configured for this server yet, please try again later.", color=0xFF2B2B, )


async def setup(bot) -> None:
    await bot.add_cog(MisdevOffice(bot))
