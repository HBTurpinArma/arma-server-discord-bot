from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import asyncpraw
from datetime import datetime, timedelta, timezone
import pytz

class Helper(commands.Cog, name="helper"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name = "signup",
        description = "Get information on how to sign up to the unit."
    )
    @app_commands.choices(battalion=[discord.app_commands.Choice(name="1st Battalion (Reforger EU)", value="am1"), discord.app_commands.Choice(name="2nd Battalion (Arma 3 EU)", value="am2"), discord.app_commands.Choice(name="3rd Battalion (Arma 3 NA)", value="am3")])
    async def signup(self, context: Context, battalion=None, post: bool=True) -> None:
        """
        Get information on how to sign up to the unit.

        :param context:
        """

        # Get the modpack links for each battalion
        sog_non_cdlc_owner_link = "https://drive.google.com/file/d/1bTUDmnRW2amZm7uKz2ShikutPv3aJrKn/view?usp=drive_link"
        sog_cdlc_owner_link = "https://drive.google.com/file/d/1TUKsKJuXpNWNIGCUOgtgEAqjPE-kcjPP/view?usp=drive_link"
        am2_non_cdlc_owner_link = "https://drive.google.com/file/d/15JpbnAF-R0yej2MILvozztJW76kRz77V/view?usp=drive_link"
        am2_cdlc_owner_link = "https://drive.google.com/file/d/1ZlPBnLEzuc42KZoEJwX83yQeekrRvWpJ/view?usp=drive_link"
        sog_non_cdlc_owner_link_direct = "https://drive.google.com/uc?export=download&id=1bTUDmnRW2amZm7uKz2ShikutPv3aJrKn"
        sog_cdlc_owner_link_direct = "https://drive.google.com/uc?export=download&id=1TUKsKJuXpNWNIGCUOgtgEAqjPE-kcjPP"
        am2_non_cdlc_owner_link_direct = "https://drive.google.com/uc?export=download&id=15JpbnAF-R0yej2MILvozztJW76kRz77V"
        am2_cdlc_owner_link_direct = "https://drive.google.com/uc?export=download&id=1ZlPBnLEzuc42KZoEJwX83yQeekrRvWpJ"

        # Get the timestamps for each battalions events
        am1_time_now = datetime.now(pytz.timezone('Europe/London'))
        am1_next_tuesday = am1_time_now + (timedelta(days=(1 - am1_time_now.weekday()) % 7))
        am1_next_tuesday = am1_next_tuesday.replace(hour=19, minute=0, second=0, microsecond=0)
        am1_next_tuesday_timestamp = f"<t:{round(am1_next_tuesday.timestamp())}:f> (<t:{round(am1_next_tuesday.timestamp())}:R>)"
        am1_next_thursday = am1_time_now + (timedelta(days=(3 - am1_time_now.weekday()) % 7))
        am1_next_thursday = am1_next_thursday.replace(hour=19, minute=0, second=0, microsecond=0)
        am1_next_thursday_timestamp = f"<t:{round(am1_next_thursday.timestamp())}:f> (<t:{round(am1_next_thursday.timestamp())}:R>)"
        am1_next_sunday = am1_time_now + (timedelta(days=(6 - am1_time_now.weekday()) % 7))
        am1_next_sunday = am1_next_sunday.replace(hour=19, minute=0, second=0, microsecond=0)
        am1_next_sunday_timestamp = f"<t:{round(am1_next_sunday.timestamp())}:f> (<t:{round(am1_next_sunday.timestamp())}:R>)"

        am2_time_now = datetime.now(pytz.timezone('Europe/London'))
        am2_next_thursday = am2_time_now + (timedelta(days=(3 - am2_time_now.weekday()) % 7))
        am2_next_thursday = am2_next_thursday.replace(hour=19, minute=0, second=0, microsecond=0)
        am2_next_thursday_timestamp = f"<t:{round(am2_next_thursday.timestamp())}:f> (<t:{round(am2_next_thursday.timestamp())}:R>)"
        am2_next_sunday = am2_time_now + (timedelta(days=(6 - am2_time_now.weekday()) % 7))
        am2_next_sunday = am2_next_sunday.replace(hour=19, minute=0, second=0, microsecond=0)
        am2_next_sunday_timestamp = f"<t:{round(am2_next_sunday.timestamp())}:f> (<t:{round(am2_next_sunday.timestamp())}:R>)"

        am3_time_now = datetime.now(pytz.timezone('Europe/London'))

        am4_time_now = datetime.now(pytz.timezone('Europe/London'))

        taw_join_info = discord.Embed(title="""Joining TAW""", description="""To begin your journey with TAW, visit [www.taw.net](https://taw.net/) and register for the Arma division. During the registration process, you will have the option to choose the battalion that best suits your preferences and schedule.\n
        Once you have completed the registration, it is crucial to reach out to the drill instructor of the selected battalion to initiate your onboarding process. They will guide you through the necessary steps and provide you with all the information you need to get started.\n
        Additionally, please ensure that you have downloaded and installed any required mods and TeamSpeak software in advance, as these are essential for participating in our operations. Each battalion may have specific requirements, so be sure to check the details provided during registration or contact your drill instructor for further assistance. Use `/signup [battalion]` to get more information on the battalion you are interested in joining.""",
        color=0xBEBEFE)

        if battalion == "am1":
            battalion_info = discord.Embed(title="""1st Battalion | Arma Reforger | EU (AM1)""", description="""When signing up make sure you select the 1st Battalion, once you've signed up be sure to reach out to an AM1 staff member who will be with you shortly.""", color=0xBEBEFE)
            battalion_info.add_field(name="Operation Times", value=f"- Tuesday @ {am1_next_tuesday_timestamp}\n- Thursday @ {am1_next_thursday_timestamp}", inline=False)
            battalion_info.add_field(name="Modpack", value="You will be prompted to download the mods when you join the server, this is only needed for Tuesday operations as Thursday's will be vanilla to allow our playstation players to join.", inline=False)
            battalion_info.add_field(name="Servers", value="- Operation | IP:``", inline=False)

            if post:
                await context.channel.send(embeds=[taw_join_info, battalion_info])
                await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
            else:
                await context.send(embeds=[taw_join_info, battalion_info], ephemeral=True)
        elif battalion == "am2":
            battalion_info = discord.Embed(title="""2nd Battalion | Arma 3 | EU (AM2)""", description="""When signing up make sure you select the 2nd Battalion, once you've signed up be sure to reach out to an AM2 staff member who will be with you shortly.""", color=0xBEBEFE)
            battalion_info.add_field(name="Operation Times", value=f"- Thursday @ {am2_next_thursday_timestamp}\n- Sunday @ {am2_next_sunday_timestamp}", inline=False)
            battalion_info.add_field(name="Modpack", value=f"""You can download the our latest presets here, selecting the Non-CDLC Owner preset if you do not own Western Sahara CDLC:\n
            [AM2 CDLC Owner Preset]({am2_cdlc_owner_link_direct}) or [AM2 Non-CDLC Owner Preset]({am2_non_cdlc_owner_link_direct})\n
            Once downloaded, import it into your launcher and subscribe to all the dependencies. You can also download additional clientside mods from our [Clientside Mods List](https://discord.com/channels/1001559795310010539/1322642580827148370).\n
            Additionally, you will need to install TeamSpeak and the TFAR plugins. If you need assistance with this process, please don't hesitate to reach out.""", inline=False)
            battalion_info.add_field(name="Servers", value="- Operation | IP:`am2.taw.net:2302`\n- RNR | IP:`am2.taw.net:2602`\n- RHQ | IP:`am2.taw.net:2802`\n- CTC | IP:`am2.taw.net:2502`", inline=False)

            if post:
                await context.channel.send(embeds=[taw_join_info, battalion_info])
                await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
            else:
                await context.send(embeds=[taw_join_info, battalion_info], ephemeral=True)
        elif battalion == "am3":
            battalion_info = discord.Embed(title="""3rd Battalion | Arma 3 | NA (AM3)""", description="""When signing up make sure you select the 3rd Battalion, once you've signed up be sure to reach out to an AM3 staff member who will be with you shortly.""", color=0xBEBEFE)
            battalion_info.add_field(name="Operation Times", value="- TBC", inline=False)
            battalion_info.add_field(name="Modpack", value=f"""You can download the our latest presets here, selecting the Non-CDLC Owner preset if you do not own Western Sahara CDLC:\n
            [AM2 CDLC Owner Preset]({am2_cdlc_owner_link_direct}) or [AM2 Non-CDLC Owner Preset]({am2_non_cdlc_owner_link_direct})\n
            Once downloaded, load it into your launcher and subscribe to all the dependencies. You can also download additional clientside mods from our [Clientside Mods List](https://discord.com/channels/1001559795310010539/1322642580827148370).\n
            Additionally, you will need to install TeamSpeak and the TFAR plugins. If you need assistance with this process, please don't hesitate to reach out.""", inline=False)
            battalion_info.add_field(name="Servers", value="- Operation | IP:`am3.taw.net:2302`", inline=False)

            if post:
                await context.channel.send(embeds=[taw_join_info, battalion_info])
                await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
            else:
                await context.send(embeds=[taw_join_info, battalion_info], ephemeral=True)
        else:
            operations = discord.Embed(title="""Operation Times""", description="""Check out the operations schedule for each battalion below, its recommended to be there atleast 15 minutes before:""", color=0xBEBEFE)
            operations.add_field(name="1st Battalion | Arma Reforger | EU (AM1)", value=f"- Tuesday @ {am1_next_tuesday_timestamp}\n- Thursday @ {am1_next_thursday_timestamp}\n- Sunday (Optional) @ {am1_next_sunday_timestamp}", inline=False)
            operations.add_field(name="2nd Battalion | Arma 3 | EU (AM2)", value=f"- Thursday @ {am2_next_thursday_timestamp}\n- Sunday @ {am2_next_sunday_timestamp}", inline=False)
            operations.add_field(name="3rd Battalion | Arma 3 | NA (AM3)", value=f"- Sunday @ TBC", inline=False)
            operations.add_field(name="4th Battalion | Arma Reforger | NA (AM4)", value=f"- Sunday @ TBC", inline=False)

            # mods = discord.Embed(title="""Modpack Installation""", description="""If you are playing on reforger, you will download mods automatically when you try and join one of the servers, for arma 3, you should install the modpack before hand as they are fairly large.""", color=0xBEBEFE)
            # mods.add_field(name="1st Battalion | Arma Reforger | EU (AM1)", value="You will be prompted to download the mods when you join the server.", inline=False)
            # mods.add_field(name="2nd Battalion | Arma 3 | EU (AM2)", value=f"""You can download the our latest presets here, selecting the Non-CDLC Owner preset if you do not own Western Sahara CDLC:\n
            # [AM2 CDLC Owner Preset]({am2_cdlc_owner_link_direct}) or [AM2 Non-CDLC Owner Preset]({am2_non_cdlc_owner_link_direct})\n
            # Once downloaded, load it into your launcher and subscribe to all the dependencies.""", inline=False)
            # mods.add_field(name="3rd Battalion | Arma 3 | NA (AM3)", value=f"""You can download the our latest presets here, selecting the Non-CDLC Owner preset if you do not own Western Sahara CDLC:\n
            # [AM2 CDLC Owner Preset]({am2_cdlc_owner_link_direct}) or [AM2 Non-CDLC Owner Preset]({am2_non_cdlc_owner_link_direct})\n
            # Once downloaded, load it into your launcher and subscribe to all the dependencies.""", inline=False)

            if post:
                await context.channel.send(embeds=[taw_join_info, operations])
                await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
            else:
                await context.send(embeds=[taw_join_info, operations], ephemeral=True)



    @commands.hybrid_command(
        name="form", 
        description="Get a link to the battalion-wide form and responses.",
    )
    async def form(self, context: Context) -> None:
        """
        Get a link to the battalion-wide form and responses.

        :param context: The hybrid command context.
        """
        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Battalion Form", style=discord.ButtonStyle.link, url = 'https://docs.google.com/forms/d/e/1FAIpQLSdykee7E-75ldEvuQ4SYBT1H1dG2v4sFCG5j85dDJYcPVDgUA/viewform'))
        buttons.add_item(discord.ui.Button(label="Responses", style=discord.ButtonStyle.link, url = 'https://docs.google.com/spreadsheets/d/1tkhHPKfpW2JxpQZq4vGv0gbVgtAIVgTiJkBoMc33d9E/edit?gid=1029717932#gid=1029717932'))
        
        embed = discord.Embed(title="""Battalion Form""", description="""Check out the form below for any suggestions, badge requests and feedback. The following options are available:""", color=0xBEBEFE)
        embed.add_field(name=f"""Battalion Suggestions""", value=f"""Send in your complaints, ideas and improvements here for BATCOM to review and address.""", inline=True)
        embed.add_field(name=f"""Newsletter""", value=f"""Got any ideas/submissions for the newsletter? Send in your snazzy recipes or posts here.""", inline=True)
        embed.add_field(name=f"""Monthly Meeting""", value=f"""Have any topics you want to discuss wider within the battalion? Post your topics here to be picked up at the montly battalion-wide meetings.""", inline=True)
        embed.add_field(name=f"""Mission Development (MISDEV)""", value=f"""Send in your feedback and ideas for missions so we can continue to improve the missions we play as a battalion.""", inline=True)
        embed.add_field(name=f"""Combat Training Centre (CTC)""", value=f"""Request for specific badges and training so that you are ready on the field.""", inline=True)
        embed.add_field(name=f"""Server Admin Office (SAO)""", value=f"""Send in your mod change requests and or any bug reports here so we can continue to improve the server.""", inline=True)
        embed.add_field(name=f"""Position Interest""", value=f"""Are you interested in any positions within the unit? Register your interest here even if the position is filled.""", inline=True)
        embed.add_field(name=f"""Arsenal Requests""", value=f"""Request changes to the in-game arsenal here if there's anything you feel is missing or should be removed.""", inline=True)
        embed.add_field(name=f"", value=f"", inline=True)

        await context.send(embed=embed, view=buttons, ephemeral=True)

    @commands.hybrid_command(
        name="social", 
        description="Get a link to all the battalion's social pages",
    )
    async def social(self, context: Context, post: bool=False) -> None:
        """
        Get a link to all the battalion's social pages

        :param context: The hybrid command context.
        """
        latest_reddit_url = await pull_recent_reddit_post_url(self, "tawarmadivision")

        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Twitch", style=discord.ButtonStyle.link, url = 'https://www.twitch.tv/tawarmadivision'))
        buttons.add_item(discord.ui.Button(label="Reddit", style=discord.ButtonStyle.link, url = latest_reddit_url))
        buttons.add_item(discord.ui.Button(label="Twitter", style=discord.ButtonStyle.link, url = 'https://x.com/TheArmaUnit'))
        buttons.add_item(discord.ui.Button(label="Instagram", style=discord.ButtonStyle.link, url = 'https://www.instagram.com/taw.armadivision/'))
        buttons.add_item(discord.ui.Button(label="Facebook", style=discord.ButtonStyle.link, url = 'https://www.facebook.com/tawarma'))
        buttons.add_item(discord.ui.Button(label="TikTok", style=discord.ButtonStyle.link, url = 'https://www.tiktok.com/@arma.division.taw?_t=8nqYd9WmCub&_r=1'))
       
        embed = discord.Embed(title="Social Links", description=f"Check out all of our social pages, be sure to give a follow and a like on our recent posts!", color=0xBEBEFE)
        embed.set_image(url="https://i.imgur.com/LOaHCV2.png")
        
        if post:
            await context.channel.send(content=f"**Be sure to like, share and follow our social media accounts!**\n Latest Reddit Post: {latest_reddit_url}\n", view=buttons)
            await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
        else:
            await context.send(content=f"**Be sure to like, share and follow our social media accounts!**\n Latest Reddit Post: {latest_reddit_url}\n", view=buttons, ephemeral=True)

async def pull_recent_reddit_post_url(self, username: str):
    reddit = asyncpraw.Reddit(
        client_id=self.bot.config['reddit']['id'],
        client_secret=self.bot.config['reddit']['secret'],
        user_agent="lookup latest submission by u/hbturpin"
    )

    user = await reddit.redditor(username)
    submissions = user.submissions.new(limit=1)

    async for link in submissions:
        return "https://www.reddit.com"+ link.permalink
    else:
        return 'https://www.reddit.com/user/tawarmadivision/'





async def setup(bot) -> None:
    await bot.add_cog(Helper(bot))



