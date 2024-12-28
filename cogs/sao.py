from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import base64
import re
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime


class ModRequestModal(discord.ui.Modal, title="Mod Request Form"):
    def __init__(self, bot, mod_type) -> None:
        mod_type_str = ""
        if mod_type == 1:
            mod_type_str = "Serverside"
        elif mod_type == 2:
            mod_type_str = "Clientside"
        elif mod_type == 3:
            mod_type_str = "Map"
        super().__init__(title=f"Mod Request Form ({mod_type_str})")
        self.bot = bot
        self.mod_type = mod_type

    mod_title = discord.ui.TextInput(style=discord.TextStyle.short, label="Mod Title", required=True, placeholder="Mod")
    mod_link = discord.ui.TextInput(style=discord.TextStyle.short, label="Steam Workshop Link", required=True, placeholder="https://steamcommunity.com/sharedfiles/filedetails/?id=")
    mod_description = discord.ui.TextInput(style=discord.TextStyle.long, label="Mod Description", required=True, placeholder="Tell us about the mod, what does it do, why would it benefit us having it?")

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.answer_title = str(self.mod_title)
        self.answer_link = str(self.mod_link)
        self.answer_description = str(self.mod_description)

        # Ensure that link provided is a workshop mod...
        pattern = r"https://steamcommunity\.com/sharedfiles/filedetails/\?id=(\d+)"
        match = re.match(pattern, self.answer_link)
        if match:
            mod_id = match.group(1)

            # Get users to mention, should be SAO + requester
            mentions = f"<@{interaction.user.id}>"
            for moderators in self.bot.config['discord']['server_admin_office']['forum_moderators']:
                mentions += f" <@&{moderators}>"
            self.answer_post = f"||{mentions}||\n# {self.answer_title}\n{self.mod_link}\n### Description\n{self.mod_description}\n\n-# Please react and discuss the requested mod below, the server admin office will prioritise mods which have the most interest. You can show interest by reacting to this post.\n"

            forum_channel = self.bot.get_channel(int(self.bot.config['discord']['server_admin_office']['forum_channel_id']))
            tags = [forum_channel.get_tag(int(self.bot.config['discord']['server_admin_office']['forum_tags']['pending_review']))]
            if self.mod_type == 1:
                tags.append(forum_channel.get_tag(int(self.bot.config['discord']['server_admin_office']['forum_tags']['serverside'])))
            elif self.mod_type == 2:
                tags.append(forum_channel.get_tag(int(self.bot.config['discord']['server_admin_office']['forum_tags']['clientside'])))
            elif self.mod_type == 3:
                tags.append(forum_channel.get_tag(int(self.bot.config['discord']['server_admin_office']['forum_tags']['map'])))

            self.post = await forum_channel.create_thread(name=f"Mod Request: {self.answer_title}", applied_tags=tags, content=self.answer_post)
            await self.post[1].add_reaction("👍")
            await self.post[1].add_reaction("👎")

            # Post to the mod discussion page for people to see
            mod_discussion_channel = self.bot.get_channel(int(self.bot.config['discord']['server_admin_office']['suggestion_channel_id']))
            await mod_discussion_channel.send(embed=discord.Embed(description=f"<@{interaction.user.id}> has submitted a mod request: <#{self.post[1].id}>", color=0xBEBEFE, ))

            # Respond to the interaction
            await interaction.response.send_message(embed=discord.Embed(description=f"Thanks for your mod suggestion, the server admin office will review it soon. You can view it here: <#{self.post[1].id}>", color=0xBEBEFE, ),
                ephemeral=True)

            # Send extra message in thread for workshop item info...
            embed, buttons = await get_workshop_embed(mod_id)
            await self.post[0].send(embed=embed, view=buttons)

        else:
            await interaction.response.send_message(
                embed=discord.Embed(description=f"The request has not be posted, you must provide a correct workshop link (https://steamcommunity.com/sharedfiles/filedetails/?id=XXXXXXXXXXX).", color=0xFF1111, ), ephemeral=True)

        self.stop()


class BugReportModal(discord.ui.Modal, title="Bug Report Form"):
    def __init__(self, bot) -> None:
        super().__init__(title="Bug Report Form")
        self.bot = bot

    bug_title = discord.ui.TextInput(style=discord.TextStyle.short, label="Bug Title", required=True, placeholder="")
    bug_description = discord.ui.TextInput(style=discord.TextStyle.long, label="Bug Description", required=True, placeholder="Tell us about the bug, what issue is happening, how does it happen? Any steps to reproduce?")

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.answer_title = str(self.bug_title)
        self.answer_description = str(self.bug_description)
        mentions = f"<@{interaction.user.id}>"
        for moderators in self.bot.config['discord']['server_admin_office']['forum_moderators']:
            mentions += f" <@&{moderators}>"
        self.answer_post = f"||{mentions}||\n# {self.answer_title}\n### Description\n{self.answer_description}\n\n-# If you have any evidence or further information, please post it below. The server admin office will look to resolve the issue as soon as possible.\n"

        forum_channel = self.bot.get_channel(int(self.bot.config['discord']['server_admin_office']['forum_channel_id']))
        tags = [forum_channel.get_tag(int(self.bot.config['discord']['server_admin_office']['forum_tags']['bug'])), forum_channel.get_tag(int(self.bot.config['discord']['server_admin_office']['forum_tags']['pending_review']))]
        self.post = await forum_channel.create_thread(name=f"Bug Report: {self.answer_title}", applied_tags=tags, content=self.answer_post)

        # Respond to the interaction
        await interaction.response.send_message(embed=discord.Embed(description=f"Thanks for your bug report, the server admin office will review it soon. You can view it here: <#{self.post[1].id}>", color=0xBEBEFE, ), ephemeral=True)

        self.stop()


class StartStopButtonSelection(discord.ui.View):
    server_options = []

    def __init__(self, server, message_context: Context, server_list: list = [], server_type: str = ""):
        super().__init__()
        self.server = server
        self.server_type = server_type
        self.server_list = server_list
        self.message_context = message_context

        for server_id in server_list:
            self.server_options.append(discord.SelectOption(label=server_id))

    @discord.ui.select(placeholder="server_id", min_values=1, max_values=1, options=server_options, custom_id="server_select")
    async def serverSelect(self, interaction: discord.Integration, select: discord.ui.Select):
        await interaction.response.defer()
        self.server_id = select.values[0]

    @discord.ui.button(label="Start", style=discord.ButtonStyle.green, custom_id="server_start")
    async def serverStartButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await ServerAdminOffice.server_start(self.server, self.message_context, self.server_id, "arma3")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.red, custom_id="server_stop")
    async def serverStopButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await ServerAdminOffice.server_stop(self.server, self.message_context, self.server_id, "arma3")


class StartStopButton(discord.ui.View):
    def __init__(self, server, message_context: Context, server_id, server_type: str = ""):
        super().__init__()
        self.server = server
        self.server_id = server_id
        self.server_type = server_type
        self.message_context = message_context

    # @discord.ui.button(label="Start", style=discord.ButtonStyle.green, custom_id="server_start")  # async def serverStartButton(self, interaction: discord.Interaction, button: discord.ui.Button):  #     await ServerAdminOffice.server_start(self.server, self.message_context, self.server_id, "arma3")

    # @discord.ui.button(label="Stop", style=discord.ButtonStyle.red, custom_id="server_stop")  # async def serverStopButton(self, interaction: discord.Interaction, button: discord.ui.Button):  #     await ServerAdminOffice.server_stop(self.server, self.message_context, self.server_id, "arma3")


class ServerForumButton(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Request Serverside Mod", style=discord.ButtonStyle.blurple, custom_id='sao_info:mod_suggest_serverside')
    async def modRequestServerButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        mod_request_modal = ModRequestModal(self.bot, 1)
        await interaction.response.send_modal(mod_request_modal)

    @discord.ui.button(label="Request Clientside Mod", style=discord.ButtonStyle.blurple, custom_id='sao_info:mod_suggest_clientside')
    async def modRequestClientButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        mod_request_modal = ModRequestModal(self.bot, 2)
        await interaction.response.send_modal(mod_request_modal)

    @discord.ui.button(label="Map Suggestion", style=discord.ButtonStyle.blurple, custom_id='sao_info:mod_suggest_map')
    async def modRequestMapButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        mod_request_modal = ModRequestModal(self.bot, 3)
        await interaction.response.send_modal(mod_request_modal)

    @discord.ui.button(label="Bug Report", style=discord.ButtonStyle.red, custom_id='sao_info:bug_report')
    async def bugReportButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        bug_report_modal = BugReportModal(self)
        await interaction.response.send_modal(bug_report_modal)


class ServerAdminOffice(commands.Cog, name="server"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.command(name="mod_info", description="Post information on a mod from the steam workshop.")
    @commands.is_owner()
    async def mod_info(self, context: Context, mod_id):
        embed, buttons = await get_workshop_embed(mod_id)
        await context.send(embed=embed, view=buttons)

    @commands.command(name="sao_info", description="Post information embed for server based suggestions.")
    @commands.is_owner()
    async def sao_info(self, context: Context) -> None:
        embed = discord.Embed(title="Ingame Forums",
                              description="If you have any mod suggestions, bug reports or want to discuss anything please use the buttons below or the following commands.\n\n`/mod_request` if you want suggest any new mods/maps to be used in the unit\n`/bug_report` if you want to report any issues from ingame\n\nYou are free to create new posts as discussions directly in the forum channel if there's any specific ingame settings you want to discuss.",
                              color=0xBEBEFE)
        await context.send(embed=embed, view=ServerForumButton(self.bot))

    @app_commands.command(name="mod_request", description="Post a mod suggestion to the server forum", )
    @app_commands.describe(type="Type of mod to suggest")
    @app_commands.choices(type=[discord.app_commands.Choice(name="Serverside", value=1), discord.app_commands.Choice(name="Clientside", value=2), discord.app_commands.Choice(name="Map", value=3)])
    async def mod_request(self, interaction: discord.Interaction, type: discord.app_commands.Choice[int]) -> None:

        # Return not configured error embed to interaction if executed in the wrong guild.
        if interaction.guild.id != self.bot.config['discord']['guild_id']:
            await interaction.response.send_message(embed=await not_configured_embed(self), ephemeral=True)
            return

        # Send the modal the user andd wait response.
        mod_request_modal = ModRequestModal(self.bot, type.value)
        await interaction.response.send_modal(mod_request_modal)
        await mod_request_modal.wait()
        interaction = mod_request_modal.interaction

    @app_commands.command(name="bug_report", description="Post a bug report to the server forum", )
    async def bug_report(self, interaction: discord.Interaction) -> None:
        # Return not configured error embed to interaction if executed in the wrong guild.
        if interaction.guild.id != self.bot.config['discord']['guild_id']:
            await interaction.response.send_message(embed=await not_configured_embed(self), ephemeral=True)
            return

        # Send the modal the user andd wait response.
        bug_report_modal = BugReportModal(self.bot)
        await interaction.response.send_modal(bug_report_modal)
        await bug_report_modal.wait()
        interaction = bug_report_modal.interaction

    param_server_id = "The 'nickname' or 'id' of the server in the web panel."
    param_server_type = "The type of server that should be referenced, options are 'arma3' or 'reforger'."

    @commands.hybrid_command(name="server_start", description="Start a specific server using the server id reference.", )
    @app_commands.describe(server_id=param_server_id, server_type=param_server_type)
    # @app_commands.choices(server_type=[
    #     discord.app_commands.Choice(name="Arma3", value="arma3"),
    #     discord.app_commands.Choice(name="Reforger", value="reforger"),
    # ])
    async def server_start(self, context: Context, server_id: str, server_type: str = "arma3") -> None:
        """
        Start a specific server using the server id reference.

        :param context: The hybrid command context.
        :param server_id: The server ID which should be started.
        :param server_type: The server address which should be referenced.
        """
        response = await self.bot.arma_server_web_admin.server_start(context.author.id, context.guild.id, server_type, server_id)
        if response.status == 200:
            embed = discord.Embed(title="Server Started", description=f"The server (`{server_id}`) has been started.", color=0xBEBEFE)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)
        elif response.status == 401:
            embed = await unauthorised_embed(self)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)
        elif response.status == 500:
            embed = await server_not_exist_embed(self, server_id)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)
        else:
            embed = await error_embed(self, response)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="server_stop", description=" Stop a specific server using the server id reference.", )
    @app_commands.describe(server_id=param_server_id, server_type=param_server_type)
    async def server_stop(self, context: Context, server_id: str, server_type: str = "arma3") -> None:
        """
        Stop a specific server using the server id reference.

        :param context: The hybrid command context.
        :param server_id: The server which should be started.
        """
        response = await self.bot.arma_server_web_admin.server_stop(context.author.id, context.guild.id, server_type, server_id)
        if response.status == 200:
            embed = discord.Embed(title="Server Stopped", description=f"The server (`{server_id}`) has been stopped.", color=0xBEBEFE)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)
        elif response.status == 401:
            embed = await unauthorised_embed(self)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)
        elif response.status == 500:
            embed = await server_not_exist_embed(self, server_id)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)
        else:
            embed = await error_embed(self, response)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="server_upload", description="Upload a .pbo mission file directly to the server.", )
    @app_commands.describe(mission_pbo="The .pbo file for mission you wish to upload to the server.", server_type=param_server_type)
    async def server_mission_upload(self, context: Context, mission_pbo: discord.Attachment, server_type: str = "arma3") -> None:
        """
        Upload a .pbo mission file directly to the server.

        :param context: The hybrid command context.
        :param mission_pbo: The server which should be started.
        """

        pass

    @commands.hybrid_command(name="server_list", description="List the available server_ids on the web panel.", )
    @app_commands.describe(server_type=param_server_type)
    async def server_list(self, context: Context, server_type: str = "arma3") -> None:
        """
        List the available server IDs to be used in other commands.

        :param context: The hybrid command context.
        """
        response = await self.bot.arma_server_web_admin.get_server_config(context.author.id, context.guild.id, server_type)
        if response.status == 200 and response.json_content:
            server_list = []
            server_ids = ""
            server_names = ""
            server_ports = ""
            for server in response.json_content:
                server_list.append(server["uid"])
                server_ids = server_ids + "`" + server["uid"] + "`" + "\n"
                server_names = server_names + "`" + server["title"] + "`" + "\n"
                server_ports = server_ports + "`" + str(server["port"]) + "`" + "\n"
            embed = discord.Embed(title="Available Servers", description="", color=0xBEBEFE)
            embed.add_field(name=f"ID", value=server_ids, inline=True)
            embed.add_field(name=f"Name", value=server_names, inline=True)
            embed.add_field(name=f"Port", value=server_ports, inline=True)
            await context.send(embed=embed, ephemeral=True, view=StartStopButtonSelection(self, message_context=context, server_type=server_type, server_list=server_list))
        elif response.status == 401:
            embed = await unauthorised_embed(self)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)
        else:
            embed = await error_embed(self, response)
            embed.set_footer(text=f"{response.url}")
            await context.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="server_status", description="Get the current server status of a specific server_id or setup a info message for the public to see.", )
    @app_commands.describe(server_id=param_server_id, server_type=param_server_type, status_channel="Setup a auto status message in a specified channel.", server_ip="Override the IP to point at a specific address rather than localhost.",
                           server_description="Any additional information you want to include in the server status.", server_modpack="A link to the modpack used for the server.")
    async def server_status(self, context: Context, server_id: str, server_type: str = "arma3", status_channel: discord.TextChannel = None, message_id: str = "", server_ip: str = "am2.taw.net", server_description: str = "",
                            server_modpack: str = "") -> None:
        """
        Get the current server status of a specific server_id or setup a info message for the public to see.

        :param context: The hybrid command context.
        """
        response = await self.bot.arma_server_web_admin.get_server_config(context.author.id, context.guild.id, server_type, server_id)
        if response.status == 200:
            server = response.json_content
            if not server is None:
                status_embed = await self.bot.server_status_embed(server_id, server_type, server_ip, server['port'], server["title"], server_description, server_modpack)
                if status_channel:  # Add status message to database to keep track.
                    if not message_id:
                        message = await status_channel.send("Querying the servers....")
                        message_id = message.id
                    response = await self.bot.database.add_server_status(context.guild.id, status_channel.id, message_id, context.author.id, server_id, server_type, server_ip, server['port'], server["title"], server_description,
                                                                         server_modpack)
                    if response:
                        await context.send(f"The status message ({message_id}) has been setup in {status_channel} sucessfully, sending update...", ephemeral=True)
                    # This update takes too long to send back to the interaction, can maybe call this back/defer. One for later maybe.
                    await self.bot.update_server_status_message(context.guild.id, status_channel.id, message_id)
                else:  # Just post current snapshot back to context
                    await context.send(embed=status_embed)
            else:  # ID/Nickname could not be found.
                status_embed = await server_not_exist_embed(self, server_id)
                await context.send(embed=status_embed, ephemeral=True)
        elif response.status == 401:
            status_embed = await unauthorised_embed(self)
            status_embed.set_footer(text=f"{response.url}")
            await context.send(embed=status_embed, ephemeral=True)
        else:
            status_embed = await error_embed(self, response)
            status_embed.set_footer(text=f"{response.url}")
            await context.send(embed=status_embed, ephemeral=True)

    @commands.hybrid_command(name="server_login", description="Setup your web panel information in discord so you can use the server commands.", )
    @app_commands.describe(server_type=param_server_type)
    async def server_login(self, context: Context, username: str, password: str, server_type: str = "arma3") -> None:
        """
        Setup your web panel information in discord so you can use the discord commands to interact with the servers directly.

        :param context: The hybrid command context.
        """
        # member = context.guild.get_member(user.id) or await context.guild.fetch_member(
        #     user.id
        # )
        user_id = context.author.id
        guild_id = context.guild.id
        response = await self.bot.database.set_server_authentication(user_id, guild_id, server_type, username, password)
        if response:
            embed = discord.Embed(description=f"Your webpanel authentication has been setup with {user_id}, {guild_id}, {server_type}, {username}, {password}.", color=0xBEBEFE)
        else:
            embed = discord.Embed(title=f"Error", description=f"""Please try again or contact the server administrator.""", color=0xE02B2B)
        await context.send(embed=embed, ephemeral=True)


async def get_workshop_embed(mod_id):
    mod_info = await get_workshop_info(mod_id)

    mod_title = mod_info["title"] if mod_info else "Unknown"
    mod_size = mod_info["size"] if mod_info else ""
    mod_description = mod_info["description"] if mod_info else "Mod not found... is it an incorrect workshop ID?"
    mod_link = mod_info["link"] if mod_info else ""
    mod_changelog = mod_info["changelog"] if mod_info else ""
    mod_dependencies = mod_info["dependencies"] if mod_info else ""
    mod_logo = mod_info["logo"] if mod_info else ""
    mod_last_updated = mod_info["last_updated"] if mod_info else ""

    embed = discord.Embed(title=mod_title, description=mod_description[:1000] + "...", url=mod_link, color=0xBEBEFE)

    # Add in size of mod field
    embed.add_field(name="Size", value=mod_size, inline=False)

    # Add in dependency field
    dependency_str = ""
    mod_count = len(mod_dependencies) + 1
    for dep in mod_dependencies:
        mod_count = mod_count - 1
        if len(dependency_str + f"[{dep[0]}]({dep[1]})\n") > 900:
            dependency_str = dependency_str + f"and {mod_count} more..."
            break
        else:
            dependency_str = dependency_str + f"[{dep[0]}]({dep[1]})\n"
    if mod_dependencies:
        embed.add_field(name="Dependencies", value=dependency_str, inline=False)

    # Add mod logo if its found
    if mod_logo:
        embed.set_thumbnail(url=mod_logo)

    # Extra buttons
    buttons = discord.ui.View()
    buttons.add_item(discord.ui.Button(label="Workshop Page", style=discord.ButtonStyle.link, url=mod_link))
    buttons.add_item(discord.ui.Button(label="Changelog", style=discord.ButtonStyle.link, url=mod_changelog))

    # Last updated footer
    if mod_last_updated:
        embed.set_footer(text=f"Last Updated: {mod_last_updated}")

    return embed, buttons


async def get_workshop_link(mod_id):
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"


async def get_workshop_changelog_link(mod_id):
    return f"https://steamcommunity.com/sharedfiles/filedetails/changelog/{mod_id}"


async def get_workshop_version(mod_id):
    PATTERN = re.compile(r"workshopAnnouncement.*?<p id=\"(\d+)\">", re.DOTALL)
    WORKSHOP_CHANGELOG_URL = "https://steamcommunity.com/sharedfiles/filedetails/changelog"

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{WORKSHOP_CHANGELOG_URL}/{mod_id}") as response:
            response_text = await response.text()
            match = PATTERN.search(response_text)
            if match:
                return datetime.fromtimestamp(int(match.group(1)))
    return datetime(1, 1, 1, 0, 0)


async def get_workshop_info(mod_id) -> dict:
    # Start session and recieve HTML data.
    async with aiohttp.ClientSession() as session:
        async with session.get(await get_workshop_link(mod_id)) as response:
            if response.status != 200:
                return {}
            response_text = await response.text()

    await session.close()

    if not response_text:
        return {}

    mod_info = {}
    cleanhtml = re.compile('<.*?>|&([a-z0-9]+|#[0-9]{1,6}|#x[0-9a-f]{1,6});')
    soup = BeautifulSoup(response_text, 'html.parser')

    # Obtain mod title
    title_tag = soup.find('title')
    if title_tag:
        mod_info['title'] = title_tag.text.strip()[16:]
    else:
        mod_info['title'] = 'Unknown'

    # Obtain mod description
    description_div = soup.find('div', class_='workshopItemDescription')
    if description_div:
        # Get all text within the description div
        description_text = description_div.get_text(separator='\n', strip=True)
        # Optionally clean and format text
        description_text = re.sub(r'\s+', ' ', description_text).replace('\n', '\n\n')  # Adjust spacing and line breaks
        mod_info['description'] = description_text
    else:
        mod_info['description'] = ""

    # Obtain mod size
    size_pattern = re.compile(r"detailsStatsContainerRight.*?<div .*?\>(.*?)</div>", re.DOTALL)
    size_match = size_pattern.search(response_text)
    if size_match:
        mod_info['size'] = re.sub(cleanhtml, '', size_match.group(1).replace("<br>", "\n").replace("</b>", "\n"))
    else:
        mod_info['size'] = "-"

    # Add on the link
    mod_info['link'] = await get_workshop_link(mod_id)

    # Add on the changelog link
    mod_info['changelog'] = await get_workshop_changelog_link(mod_id)

    # Obtain mod dependencies if any;
    required_items_links = soup.find_all('a', {'data-subscribed': '0'})
    required_items = [[item.find('div', class_='requiredItem').get_text(strip=True), item['href']] for item in required_items_links]
    if required_items:
        mod_info['dependencies'] = required_items
    else:
        mod_info['dependencies'] = []

    # Obtain mod icon link from workshop:
    icon_img_tag = soup.find('img', id='previewImageMain', class_='workshopItemPreviewImageMain')
    mod_info['logo'] = icon_img_tag['src'] if icon_img_tag else None  # Get the 'src' of the img tag, or None if not found

    # Obtain last updated date
    mod_info['last_updated'] = await get_workshop_version(mod_id)

    return mod_info


async def get_user_authentication(self, user_id: str, guild_id: str, server_type: str = "arma3") -> None:
    authentication = await self.bot.database.get_server_authentication(user_id, guild_id, server_type)
    # print(authentication, base64.b64encode(f"{authentication['username']}:{authentication['password']}".encode()).decode())
    return base64.b64encode(f"{authentication['username']}:{authentication['password']}".encode()).decode()


async def unauthorised_embed(self):
    return discord.Embed(title=f"Unauthorised Access", description=f"""Your login details for the web panel are either incorrect or you do not have the required permissions.
        You can use /server_login [username] [password] to set up your details to use this bot.
        """, color=0xE02B2B, )


async def error_embed(self, response):
    return discord.Embed(title=f"{response.reason} ({response.status})", description=f"{response.content}", color=0xE02B2B, )


async def server_not_exist_embed(self, server_id=""):
    return discord.Embed(title=f"`{server_id}` Not Found", description=f"Use /server_list to see the available servers.", color=0xE02B2B, )


async def not_configured_embed(self):
    return discord.Embed(title=f"Command Unavailable", description=f"This command hasn't been configured for this server yet, please try again later.", color=0xFF2B2B, )


async def setup(bot) -> None:
    await bot.add_cog(ServerAdminOffice(bot))
