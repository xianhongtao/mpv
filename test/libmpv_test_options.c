/*
 * This file is part of mpv.
 *
 * mpv is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * mpv is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with mpv.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "libmpv_common.h"

// Dummy values for test_options_and_properties
static const char *str = "string";
static int flag = 1;
static int64_t int_ = 20;
static double double_ = 1.5;

static void check_results(const char *properties[], enum mpv_format formats[])
{
    for (int i = 0; properties[i]; i++) {
        switch (formats[i]) {
        case MPV_FORMAT_STRING:
            check_string(properties[i], str);
            break;
        case MPV_FORMAT_FLAG:
            check_flag(properties[i], flag);
            break;
        case MPV_FORMAT_INT64:
            check_int(properties[i], int_);
            break;
        case MPV_FORMAT_DOUBLE:
            check_double(properties[i], double_);
            break;
        }
    }
}

static void set_options_and_properties(const char *options[], const char *properties[],
                                              enum mpv_format formats[])
{
    for (int i = 0; options[i]; i++) {
        switch (formats[i]) {
        case MPV_FORMAT_STRING:
            set_option_or_property(options[i], formats[i], &str, true);
            set_option_or_property(properties[i], formats[i], &str, false);
            break;
        case MPV_FORMAT_FLAG:
            set_option_or_property(options[i], formats[i], &flag, true);
            set_option_or_property(properties[i], formats[i], &flag, false);
            break;
        case MPV_FORMAT_INT64:
            set_option_or_property(options[i], formats[i], &int_, true);
            set_option_or_property(properties[i], formats[i], &int_, false);
            break;
        case MPV_FORMAT_DOUBLE:
            set_option_or_property(options[i], formats[i], &double_, true);
            set_option_or_property(properties[i], formats[i], &double_, false);
            break;
        }
    }
}

// Ensure that setting options/properties work correctly and
// have the expected values.
static void test_options_and_properties(void)
{
    // Order matters. string -> flag -> int -> double (repeat)
    // One for set_option the other for set_property
    const char *options[] = {
        "screen-name",
        "save-position-on-quit",
        "cursor-autohide",
        "speed",
        NULL
    };

    const char *properties[] = {
        "fs-screen-name",
        "shuffle",
        "sub-pos",
        "window-scale",
        NULL
    };

    // Must match above ordering.
    enum mpv_format formats[] = {
        MPV_FORMAT_STRING,
        MPV_FORMAT_FLAG,
        MPV_FORMAT_INT64,
        MPV_FORMAT_DOUBLE,
    };

    set_options_and_properties(options, properties, formats);

    check_results(options, formats);
    check_results(properties, formats);

    // Ensure the format is still MPV_FORMAT_FLAG for these property types.
    mpv_node result_node;
    get_property("idle-active", MPV_FORMAT_NODE, &result_node);
    if (result_node.format != MPV_FORMAT_FLAG)
        fail("Node: expected mpv format '%d' but got '%d'!\n", MPV_FORMAT_FLAG, result_node.format);

    // Always should be true.
    if (result_node.u.flag != 1)
        fail("Node: expected 1 but got %d'!\n", result_node.u.flag);
}

static void test_video_sync(void)
{
    const char *modes[] = {
        "pts", "audio", "display-resample", "display-resample-vdrop",
        "display-resample-desync", "display-tempo", "display-adrop",
        "display-vdrop", "display-desync", "desync", "pts", "audio",
    };
    for (int i = 0; i < sizeof(modes) / sizeof(modes[0]); i++) {
        set_property_string("video-sync", modes[i]);
        check_string("video-sync", modes[i]);
    }
    // An intentional parse error must not reach wrap_wait_event(), which
    // treats unexpected error-level log messages as a test failure.
    if (mpv_request_log_messages(ctx, "no") < 0)
        fail("Could not disable logs for invalid-option test\n");
    int ret = mpv_set_property_string(ctx, "video-sync", "invalid-pts-mode");
    if (ret >= 0)
        fail("Invalid video-sync mode was not rejected\n");
    if (mpv_request_log_messages(ctx, "debug") < 0)
        fail("Could not restore test logs\n");
    check_string("video-sync", "audio");
}

int main(int argc, char *argv[])
{
    if (argc != 1)
        return 1;

    ctx = mpv_create();
    if (!ctx)
        return 1;

    atexit(exit_cleanup);

    const char *sync = "pts";
    set_option_or_property("video-sync", MPV_FORMAT_STRING, &sync, true);
    initialize();
    check_string("video-sync", "pts");

    const char *fmt = "================ TEST: %s ================\n";
    printf(fmt, "test_options_and_properties");
    test_options_and_properties();
    printf(fmt, "test_video_sync");
    test_video_sync();
    printf("================ SHUTDOWN ================\n");

    command_string("quit");
    while (wrap_wait_event()->event_id != MPV_EVENT_SHUTDOWN) {}

    return 0;
}
