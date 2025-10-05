from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import re
from datetime import datetime, timedelta

class MissionSubmissionModal(discord.ui.Modal, title="Mission Submission Form"):
    def __init__(self, bot, mission_pbo: discord.Attachment) -> None:
        super().__init__(title=f"Mission Submission Form")
        self.bot = bot
        self.mission_pbo = mission_pbo

    mission_title = discord.ui.TextInput(style=discord.TextStyle.short, label="Mission Title", required=True, placeholder="Operation Stinky")
    mission_description = discord.ui.TextInput(style=discord.TextStyle.long, label="Description", required=False, placeholder="A short description of the mission and objectives.", max_length=1600)
    # mission_map = discord.ui.TextInput(style=discord.TextStyle.short, label="Map", required=True, placeholder="Chernarus/Altis/Stratis")
    mission_briefing = discord.ui.TextInput(style=discord.TextStyle.short, label="Briefing", required=False, placeholder="https://docs.google.com/document/d/...")
    mission_conceptboard = discord.ui.TextInput(style=discord.TextStyle.short, label="Conceptboard", required=False, placeholder="https://app.conceptboard.com/board/...")
    mission_notes = discord.ui.TextInput(style=discord.TextStyle.long, label="Notes", required=False, placeholder="Size? Faction? Uniform? Enemy? Anything else?", max_length=1600)

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
        answer_post = f"||{mentions}||\n# {answer_title}\n{answer_description}\n\n**Map:** {answer_map}\n**Briefing:** {answer_briefing}\n**Conceptboard:** {answer_conceptboard}\n\n"

        forum_channel = self.bot.get_channel(int(self.bot.config['discord']['mission_development']['forum_channel_id']))
        tags = [forum_channel.get_tag(int(self.bot.config['discord']['mission_development']['forum_tags']['pending_review']))]

        # Prepare pbo file for upload
        pbo_file = await self.mission_pbo.to_file(use_cached=False, spoiler=False)

        # Create thread
        self.post = await forum_channel.create_thread(name=f"Mission: {answer_title}", applied_tags=tags, content=answer_post, file=pbo_file)

        # Lets ping the mission author and add a quick note to ensure all information has been provided.
        await self.post[0].send(f"**Additional Notes:**\n{answer_notes}")

        # Lets ping the mission author and add a quick note to ensure all information has been provided.
        await self.post[0].send(f"<@{interaction.user.id}>, thanks for your submission, can you ensure all the below questions have been answered in the above thread, if not add extra information below.\n- Whats is the size of the mission, is it fit for squad, platoon or battalion?\n- What faction are we playing as? What uniform should be used?\n- What assets are available? Are there any vehicles, air support?\n- What faction are we playing against? What's their name in Zeus/Editor?\n- What is the enemy strength? How many vehicles, infantry, air?\n- Has the concept board been updated with the latest information?\n- Is there a conceptboard with visuals for HVTs/objectives?\n\nAdditionally if you have any questions or need to upload new versions, do just add to this thread directly.")

        # Lets ping the head of misdev to ensure they are aware of the submission and can review it.
        mentions = ""
        for moderators in self.bot.config['discord']['mission_development']['forum_moderators']:
           mentions += f" <@&{moderators}>"

        await self.post[0].send(f"""{mentions} will look to review this mission submission as soon as possible, please be patient.""")

        # Respond to the interaction
        if interaction.response:
            await interaction.response.send_message(embed=discord.Embed(
                    description=f"Thanks for your mission submission, MisDev will review it and schedule it to be played. You can see the mission submission thread here: <#{self.post[1].id}>",
                    color=0xBEBEFE,
            ), ephemeral=True)

        self.stop()

class MissionFeedbackModal(discord.ui.Modal, title="Mission Feedback Form"):
    def __init__(self, bot) -> None:
        super().__init__(title=f"Mission Feedback Form")
        self.bot = bot

    mission_feedback_good = discord.ui.TextInput(style=discord.TextStyle.long, label="What did you find good about the Operation?", required=True, placeholder="- Favourite parts of the mission?", max_length=2200)
    mission_feedback_bad = discord.ui.TextInput(style=discord.TextStyle.long, label="What could be improved?", required=True, placeholder="- Was the mission easy to follow?\n- What could they do better?\n- Issues with server performance?", max_length=2200)
    mission_feedback_notes = discord.ui.TextInput(style=discord.TextStyle.long, label="Anything else?", required=False, placeholder="", max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        answer_good = str(self.mission_feedback_good) or ""
        answer_bad = str(self.mission_feedback_bad) or ""
        answer_user = f"<@{interaction.user.id}>"

        await interaction.channel.send(f"# Feedback from {answer_user}\n\n**What did you find good about the Operation?**\n{answer_good}\n\n**What could be improved?**\n{answer_bad}\n\n**Anything else?**\n{self.mission_feedback_notes or '-'}")

        # Respond to the interaction
        if interaction.response:
            await interaction.response.send_message(embed=discord.Embed(
                description=f"Thanks for your mission feedback, be sure to have given your ratings in the thread above as well!",
                color=0xBEBEFE), ephemeral=True)

        self.stop()

class MissionFeedbackButton(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Give Feedback", style=discord.ButtonStyle.blurple, custom_id='misdev_feedback:mission_feedback')
    async def missionFeedbackButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        mission_feedback_modal = MissionFeedbackModal(self.bot)
        await interaction.response.send_modal(mission_feedback_modal)

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

    @app_commands.command(name="mission_feedback", description="Feedback on a mission just played, be sure to run this in the forum thread directly.", )
    async def mission_feedback(self, interaction: discord.Interaction) -> None:
        # Return not configured error embed to interaction if executed in the wrong guild.
        if interaction.guild.id != self.bot.config['discord']['guild_id']:
            await interaction.response.send_message(embed=await not_configured_embed(self), ephemeral=True)
            return

        if interaction.channel.id != int(self.bot.config['discord']['mission_development']['forum_channel_id']):
            await interaction.response.send_message(embed=discord.Embed(description=f"Ensure that you run this command in a mission forum thread.", color=0xFF2B2B), ephemeral=True)
            return

        # Send the modal the user and wait for a response.
        mission_submission_modal = MissionFeedbackModal(self.bot)
        await interaction.response.send_modal(mission_submission_modal)


    @app_commands.command(name="mission_played", description="Set a mission state to played and send out the feedback forms and polls to all members.", )
    async def mission_played(self, interaction: discord.Interaction) -> None:
        context = await commands.Context.from_interaction(interaction)
        ## Obtain the channel id and ensure it from the forum channel.
        self.bot.logger.info(f"Mission played command executed by {interaction.user.id} in channel {interaction.channel.parent_id}.")

        # Return not configured error embed to interaction if executed in the wrong guild.
        if interaction.guild.id != self.bot.config['discord']['guild_id']:
            await context.send(embed=await not_configured_embed(self), ephemeral=True)
            return

        if interaction.channel.parent_id != int(self.bot.config['discord']['mission_development']['forum_channel_id']):
            await context.send(embed=discord.Embed(description=f"Ensure that you run this command in a mission forum thread.", color=0xFF2B2B), ephemeral=True)
            return

        # Ensure the user has the forum moderator role.
        if not any(role.id in self.bot.config['discord']['mission_development']['forum_moderators'] for role in interaction.user.roles):
            # Get the context of the interaction to respond to it.
            await context.send(embed=discord.Embed(description=f"Only misdev can run this command.", color=0xFF2B2B), ephemeral=True)
            return

        # Set the forum thread tags to be played.
        await interaction.channel.add_tags(interaction.channel.parent.get_tag(int(self.bot.config['discord']['mission_development']['forum_tags']['played'])))
        await interaction.channel.add_tags(interaction.channel.parent.get_tag(int(self.bot.config['discord']['mission_development']['forum_tags']['accepted'])))
        await interaction.channel.remove_tags(interaction.channel.parent.get_tag(int(self.bot.config['discord']['mission_development']['forum_tags']['pending_review'])))
        await interaction.channel.remove_tags(interaction.channel.parent.get_tag(int(self.bot.config['discord']['mission_development']['forum_tags']['scheduled'])))


        # Send the mission concept rating poll.
        poll_concept = discord.Poll(question="Rate the mission concept!", duration=timedelta(days=7))
        poll_concept.add_answer(text="1")
        poll_concept.add_answer(text="2")
        poll_concept.add_answer(text="3")
        poll_concept.add_answer(text="4")
        poll_concept.add_answer(text="5")
        await context.send(poll=poll_concept)

        # Send the zeus rating poll.
        poll_zeus = discord.Poll(question="Rate your mission zeuses!", duration=timedelta(days=7))
        poll_zeus.add_answer(text="1")
        poll_zeus.add_answer(text="2")
        poll_zeus.add_answer(text="3")
        poll_zeus.add_answer(text="4")
        poll_zeus.add_answer(text="5")
        await context.send(poll=poll_zeus)

        # Send the mission concept rating poll.
        poll_nco = discord.Poll(question="Rate your NCOs & leadership!", duration=timedelta(days=7))
        poll_nco.add_answer(text="1")
        poll_nco.add_answer(text="2")
        poll_nco.add_answer(text="3")
        poll_nco.add_answer(text="4")
        poll_nco.add_answer(text="5")
        await context.send(poll=poll_nco)

        mentions = ""
        for roles in self.bot.config['discord']['am2']['roles']['members']:
            mentions += f" <@&{roles}>"

        # Send the feedback form embed with button.
        embed = discord.Embed(title="Provide your mission feedback!", description=f"All members are encouraged to give feedback, at a minimum please give your ratings to the mission, zeuses and NCO above. The below feedback form can be used to provide more details for mission feedback.\n\nIf the below button doesn't work run the `/mission_feedback` command in this thread.", color=0xBEBEFE)
        await context.send(content=mentions, embed=embed, view=MissionFeedbackButton(self.bot))


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
