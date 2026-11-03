package org.telegram.messenger;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.telegram.tgnet.TLObject;
import org.telegram.tgnet.TLRPC;
import org.telegram.tgnet.tl.TL_account;

import java.util.ArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

@RunWith(AndroidJUnit4.class)
public class MessengerApiClientIntegrationTest {

    @Test
    public void twoAccountsLoginSearchCreateSendReceiveAndStreamMessage() throws Exception {
        assertTrue("The integration test requires the REST/SSE adapter", BuildVars.MESSENGER_API_ENABLED);
        MessengerApiClient aliceClient = MessengerApiClient.getInstance(0);
        MessengerApiClient bobClient = MessengerApiClient.getInstance(1);
        aliceClient.endSession();
        bobClient.endSession();

        NotificationCenter center = NotificationCenter.getInstance(1);
        CountDownLatch incomingMessage = new CountDownLatch(1);
        NotificationCenter.NotificationCenterDelegate observer = (id, account, args) -> {
            if (id != NotificationCenter.didReceiveNewMessages || account != 1 || args.length < 2) return;
            if (!(args[1] instanceof ArrayList)) return;
            ArrayList<?> messages = (ArrayList<?>) args[1];
            for (Object value : messages) {
                if (value instanceof MessageObject && "emulator SSE round trip".equals(((MessageObject) value).messageOwner.message)) {
                    incomingMessage.countDown();
                    return;
                }
            }
        };
        InstrumentationRegistry.getInstrumentation().runOnMainSync(
                () -> center.addObserver(observer, NotificationCenter.didReceiveNewMessages));

        try {
            TLRPC.User alice = login(aliceClient, "+999000000901");
            TLRPC.User bob = login(bobClient, "+999000000902");
            aliceClient.onAuthenticated(alice.id);
            bobClient.onAuthenticated(bob.id);
            InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> {
                UserConfig.getInstance(0).setCurrentUser(alice);
                UserConfig.getInstance(1).setCurrentUser(bob);
                MessagesController.getInstance(0).putUser(alice, false);
                MessagesController.getInstance(1).putUser(bob, false);
            });

            updateFirstName(aliceClient, "Alice Android Test");
            updateFirstName(bobClient, "Bob Android Test");

            TLRPC.TL_messages_getDialogs dialogsRequest = new TLRPC.TL_messages_getDialogs();
            dialogsRequest.limit = 50;
            execute(bobClient, dialogsRequest, TLRPC.messages_Dialogs.class);

            TLRPC.TL_contacts_search search = new TLRPC.TL_contacts_search();
            search.q = "Bob Android Test";
            search.limit = 20;
            TLRPC.TL_contacts_found found = execute(aliceClient, search, TLRPC.TL_contacts_found.class);
            assertEquals(1, found.results.size());
            assertEquals(alice.id, UserConfig.getInstance(0).getClientUserId());
            assertEquals(bob.id, found.users.get(0).id);

            TLRPC.TL_messages_getHistory createConversation = new TLRPC.TL_messages_getHistory();
            createConversation.peer = inputPeer(bob);
            createConversation.limit = 20;
            TLRPC.messages_Messages initialHistory = execute(aliceClient, createConversation, TLRPC.messages_Messages.class);
            assertTrue(initialHistory.messages.isEmpty());

            TLRPC.TL_messages_sendMessage send = new TLRPC.TL_messages_sendMessage();
            send.peer = inputPeer(bob);
            send.message = "emulator SSE round trip";
            send.random_id = System.nanoTime();
            TLRPC.TL_updateShortSentMessage sent = execute(aliceClient, send, TLRPC.TL_updateShortSentMessage.class);
            assertTrue(sent.id > 0);

            assertTrue("Recipient should process the live account SSE event", incomingMessage.await(15, TimeUnit.SECONDS));

            TLRPC.TL_messages_getHistory readHistory = new TLRPC.TL_messages_getHistory();
            readHistory.peer = inputPeer(alice);
            readHistory.limit = 20;
            TLRPC.messages_Messages received = execute(bobClient, readHistory, TLRPC.messages_Messages.class);
            assertEquals(1, received.messages.size());
            assertEquals("emulator SSE round trip", received.messages.get(0).message);
        } finally {
            InstrumentationRegistry.getInstrumentation().runOnMainSync(
                    () -> center.removeObserver(observer, NotificationCenter.didReceiveNewMessages));
            aliceClient.endSession();
            bobClient.endSession();
        }
    }

    private static TLRPC.User login(MessengerApiClient client, String phone) throws Exception {
        TLRPC.TL_auth_sendCode request = new TLRPC.TL_auth_sendCode();
        request.phone_number = phone;
        TLRPC.TL_auth_sentCodeSuccess response = execute(client, request, TLRPC.TL_auth_sentCodeSuccess.class);
        assertNotNull(response.authorization);
        assertNotNull(response.authorization.user);
        return response.authorization.user;
    }

    private static void updateFirstName(MessengerApiClient client, String firstName) throws Exception {
        TL_account.updateProfile request = new TL_account.updateProfile();
        request.flags = 1;
        request.first_name = firstName;
        TLRPC.User updated = execute(client, request, TLRPC.User.class);
        assertEquals(firstName, updated.first_name);
    }

    private static TLRPC.TL_inputPeerUser inputPeer(TLRPC.User user) {
        TLRPC.TL_inputPeerUser peer = new TLRPC.TL_inputPeerUser();
        peer.user_id = user.id;
        peer.access_hash = user.access_hash;
        return peer;
    }

    private static <T extends TLObject> T execute(MessengerApiClient client, TLObject request, Class<T> expectedType) throws Exception {
        CountDownLatch completed = new CountDownLatch(1);
        AtomicReference<TLObject> response = new AtomicReference<>();
        AtomicReference<TLRPC.TL_error> error = new AtomicReference<>();
        client.executeRequest(request, (result, requestError) -> {
            response.set(result);
            error.set(requestError);
            completed.countDown();
        });
        assertTrue("REST request timed out: " + request.getClass().getSimpleName(), completed.await(30, TimeUnit.SECONDS));
        if (error.get() != null) {
            fail(request.getClass().getSimpleName() + " failed: " + error.get().code + " " + error.get().text);
        }
        assertTrue("Unexpected response for " + request.getClass().getSimpleName() + ": " + response.get(), expectedType.isInstance(response.get()));
        return expectedType.cast(response.get());
    }
}
