(function() {
    'use strict';

    // ----------------------------------------------------
    // Chat Details
    // ----------------------------------------------------

    let chat = {
        name:  undefined,
        email: undefined,
        emmaa_model: undefined,
        myChannel: undefined,
    }


    // ----------------------------------------------------
    // Targeted Elements
    // ----------------------------------------------------

    const chatPage   = $(document)
    const chatWindow = $('.chatbubble')
    const chatBody   = chatWindow.find('.chat-window')


    // ----------------------------------------------------
    // Register helpers
    // ----------------------------------------------------

    let helpers = {
    // --------------------------------------------------------------------
    // Show the appropriate display screen. Login screen or Chat screen.
    // --------------------------------------------------------------------

        ShowAppropriateChatDisplay: function () {
            (chat.name) ? helpers.ShowChatRoomDisplay() : helpers.ShowChatInitiationDisplay()
        },

    // ----------------------------------------------------
    // Show the enter details form.
    // ----------------------------------------------------

        ShowChatInitiationDisplay: function () {
            chatBody.find('.chats').removeClass('active')
            chatBody.find('.login-screen').addClass('active')
        },

    // ----------------------------------------------------
    // Show the chat room messages display.
    // ----------------------------------------------------

        ShowChatRoomDisplay: function () {
            chatBody.find('.chats').addClass('active')
            chatBody.find('.login-screen').removeClass('active')

            setTimeout(function(){
                chatBody.find('.loader-wrapper').hide()
                chatBody.find('.input, .messages').show()
            }, 2000)
        },

    // ----------------------------------------------------
    // Append a message to the chat messages UI.
    // ----------------------------------------------------

        NewChatMessage: function (message) {
            if (message !== undefined) {
                const messageClass = message.sender !== chat.email ? 'support' : 'user'

                chatBody.find('ul.messages').append(
                    `<li class="clearfix message ${messageClass}">
                        <div class="sender">${message.name}</div>
                        <div class="message">${message.text}</div>
                    </li>`
                )


                chatBody.scrollTop(chatBody[0].scrollHeight)
            }
        },

    // ----------------------------------------------------
    // Send a message to the chat channel.
    // ----------------------------------------------------

        SendMessageToSupport: function (evt) {

            evt.preventDefault()

            let createdAt = new Date()
            createdAt = createdAt.toLocaleString()

            const message = $('#newMessage').val().trim()

            chat.myChannel.trigger('client-guest-new-message', {
                'sender': chat.name,
                'email': chat.email,
                'emmaa_model': chat.emmaa_model,
                'text': message,
                'createdAt': createdAt
            });

            helpers.NewChatMessage({
                'text': message,
                'name': chat.name,
                'emmaa_model': chat.emmaa_model,
                'sender': chat.email
            })

            console.log("Message added!")

            $('#newMessage').val('')
        },

    // ----------------------------------------------------
    // Logs user into a chat session.
    // ----------------------------------------------------

        LogIntoChatSession: function (evt) {
            const email = $('#email').val().trim().toLowerCase()
            const name  = email
            const emmaa_model = $('#emmaa_model').val().trim()

            // Disable the form
            chatBody.find('#loginScreenForm input, #loginScreenForm button').attr('disabled', true)

            if ((name !== '' && name.length >= 3) && (email !== '' && email.length >= 5)) {
                axios.post('/new/guest', {name, email, emmaa_model}).then(response => {
                    chat.name = name
                    chat.email = email
                    chat.emmaa_model = emmaa_model
                    chat.myChannel = pusher.subscribe('private-' + response.data.email);
                    helpers.ShowAppropriateChatDisplay()
                })

            } else {
                alert('Enter a valid name and email.')
            }

            evt.preventDefault()
        }
    }

    // ------------------------------------------------------------------
    // Listen for a new message event from the admin
    // ------------------------------------------------------------------

    pusher.bind('client-support-new-message', function(data){
        helpers.NewChatMessage(data)
    })


    // ----------------------------------------------------
    // Register page event listeners
    // ----------------------------------------------------

    chatPage.ready(helpers.ShowAppropriateChatDisplay)
    chatWindow.toggleClass('opened')
    chatBody.find('#loginScreenForm').on('submit', helpers.LogIntoChatSession)
    chatBody.find('#messageSupport').on('submit', helpers.SendMessageToSupport)
}())
