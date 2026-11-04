package org.telegram.platform.repo;

import org.telegram.platform.PlatformException;

/** Result callback for repository calls; always invoked on the repository executor. */
public interface Callback<T> {

    void onSuccess(T value);

    void onError(PlatformException error);
}
